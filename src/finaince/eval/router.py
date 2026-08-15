"""Eval router by (dialect, data_backend). Does not merge engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalRequest:
    expression: str
    dialect: str
    data_backend: str = "local"
    universe: str = "local_panel"
    start: str | None = None
    end: str | None = None
    cost_bps: float | None = None


@dataclass
class EvalResult:
    ok: bool
    dialect: str
    data_backend: str
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    translatable: bool = False
    alt_text: str | None = None
    warnings: list[str] = field(default_factory=list)


def listed_operators() -> set[str]:
    import re
    from pathlib import Path

    path = Path(__file__).with_name("operators.yaml")
    if not path.exists():
        return {"Rank", "Ref", "Delta", "Mean", "Std", "Corr"}
    names: set[str] = set()
    for match in re.finditer(r"(?:name|aliases|maps_to):\s*\[?([A-Za-z0-9_, ]+)\]?", path.read_text()):
        for part in match.group(1).split(","):
            token = part.strip()
            if token:
                names.add(token)
    return names or {"Rank", "Ref", "Delta", "Mean", "Std", "Corr"}


def is_listed(expression: str) -> bool:
    import re

    names = listed_operators()
    found = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression)
    return all(name in names for name in found)


def evaluate(req: EvalRequest) -> EvalResult:
    result = _evaluate(req)
    try:
        from finaince.trace import append_event

        append_event(
            "eval",
            metrics={"ok": result.ok, **(result.metrics or {})},
            error=result.error,
            summary=f"eval {req.dialect} ok={result.ok}",
        )
    except Exception:
        pass
    return result


def _evaluate(req: EvalRequest) -> EvalResult:
    from finaince.eval.dialects import attach_translation
    from finaince.obs import emit

    translation = attach_translation(req.expression, req.dialect)
    alt_text = translation.get("alt_text") if isinstance(translation.get("alt_text"), str) else None
    translatable = bool(translation.get("translatable"))
    if req.dialect == "repro_polars":
        from reproagent.reproducer.polars_engine import validate_expression

        payload = validate_expression(req.expression)
        if not payload.get("valid"):
            emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=False)
            return EvalResult(
                ok=False,
                dialect=req.dialect,
                data_backend=req.data_backend,
                metrics={"validation": payload},
                error=";".join(payload.get("errors") or []),
                translatable=translatable,
                alt_text=alt_text,
                warnings=[],
            )
        import os

        from finaince.runtime import default_backtest_window, packaged_local_panel
        from finaince.settings import reproagent_runtime_settings
        from reproagent.reproducer.backtest_bundle import build_backtest_bundle

        packed = packaged_local_panel()
        if packed is not None and not (os.environ.get("FINAINCE_LOCAL_DATA_PATH") or "").strip():
            os.environ.setdefault("LOCAL_DATA_PATH", str(packed))
            os.environ.setdefault("FINAINCE_LOCAL_DATA_PATH", str(packed))
            os.environ.setdefault("AIMINER_LOCAL_DATA_PATH", str(packed))
        settings = reproagent_runtime_settings()
        backend = (req.data_backend or "local").strip().lower()
        if backend == "ricequant":
            settings = settings.model_copy(update={"data_source": "ricequant"})
        elif backend == "local":
            settings = settings.model_copy(update={"data_source": "local"})
        window = default_backtest_window(settings.data_source)
        start = req.start or window["start_date"].isoformat()
        end = req.end or window["end_date"].isoformat()
        from finaince.runtime import default_universe, local_panel_is_thin

        universe = req.universe or default_universe(settings.data_source)
        warnings: list[str] = []
        if backend == "local" and local_panel_is_thin():
            warnings.append("thin_panel")
            if universe in {"csi300", "hs300", "csi500", "all"}:
                universe = "local_panel"
        cost_bps = req.cost_bps
        bundle_kwargs: dict[str, Any] = {
            "start_date": start,
            "end_date": end,
            "universe": universe,
            "settings": settings,
        }
        if cost_bps is not None:
            bundle_kwargs["transaction_cost_bps"] = float(cost_bps)
        bt = build_backtest_bundle(req.expression, **bundle_kwargs)
        rows = bt.get("rows") or 0
        ic = bt.get("ic_mean")
        ok = ic is not None and int(rows) > 0
        daily_returns: dict[str, float] = {}
        equity_path = bt.get("equity_curve_path")
        if equity_path:
            try:
                from pathlib import Path as EquityPath

                from reproagent.reproducer.metrics import serialize_equity_returns

                daily_returns = serialize_equity_returns(EquityPath(str(equity_path)))
            except Exception:
                daily_returns = {}
        try:
            from finaince.catalog.audit import append as audit_append

            audit_append("eval", {"expression": req.expression, "ok": ok, "rows": rows})
        except Exception:
            pass
        emit(
            "eval_finished",
            dialect=req.dialect,
            data_backend=backend or settings.data_source,
            ok=ok,
            thin_panel="thin_panel" in warnings,
        )
        return EvalResult(
            ok=ok,
            dialect=req.dialect,
            data_backend=backend or settings.data_source,
            metrics={
                "validation": payload,
                "ic_mean": ic,
                "ic_ir": bt.get("ic_ir"),
                "sharpe_ratio": bt.get("sharpe_ratio"),
                "max_drawdown": bt.get("max_drawdown"),
                "long_short_annual_return": bt.get("long_short_annual_return"),
                "backtest_id": bt.get("backtest_id"),
                "rows": rows,
                "start": start,
                "end": end,
                "data_source": settings.data_source,
                "universe_claim": universe,
                "alt_text": alt_text,
                "daily_returns": daily_returns,
                "transaction_cost_bps": bt.get("transaction_cost_bps"),
            },
            translatable=translatable,
            alt_text=alt_text,
            warnings=warnings,
            error=None if ok else "empty_or_missing_ic",
        )
    if req.dialect == "qlib":
        import os

        from finaince.eval.qlib_subprocess import qlib_subprocess_enabled, run_qlib_eval

        if qlib_subprocess_enabled():
            backend = (req.data_backend or "qlib").strip().lower()
            if backend in {"", "auto"}:
                backend = "qlib"
            payload = run_qlib_eval(
                req.expression,
                start=req.start,
                end=req.end,
                universe=req.universe,
                data_backend=backend,
            )
            ok = bool(payload.get("ok"))
            emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=ok)
            return EvalResult(
                ok=ok,
                dialect=req.dialect,
                data_backend=req.data_backend,
                metrics={
                    **(payload.get("metrics") or {}),
                    "via": "qlib_subprocess",
                    "universe_claim": req.universe,
                    "alt_text": alt_text,
                },
                translatable=translatable,
                alt_text=alt_text,
                error=None if ok else str(payload.get("error") or "qlib_subprocess_failed"),
            )
        placeholder_ok = os.environ.get("FINAINCE_QLIB_PLACEHOLDER_OK", "").strip() == "1"
        ok = bool(placeholder_ok and req.expression.strip())
        emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=ok)
        return EvalResult(
            ok=ok,
            dialect=req.dialect,
            data_backend=req.data_backend,
            metrics={
                "note": "qlib dialect is a 3.12 placeholder; live AlphaEval stays on 3.10",
                "universe_claim": req.universe,
                "alt_text": alt_text,
            },
            translatable=translatable,
            alt_text=alt_text,
            error=None if placeholder_ok else "qlib_placeholder",
        )
    emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=False)
    return EvalResult(
        ok=False,
        dialect=req.dialect,
        data_backend=req.data_backend,
        error="unknown dialect",
        translatable=translatable,
        alt_text=alt_text,
    )

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
    cost_bps: float = 0.0


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


_OPERATORS: set[str] | None = None
_OPERATORS_MTIME: int | None = None

_PANEL_CACHE: dict[tuple, Any] = {}
_PANEL_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0}
_PANEL_CACHE_INSTALLED = False


def _panel_cache_disabled() -> bool:
    import os

    return os.environ.get("FINAINCE_PANEL_CACHE", "").strip().lower() in {"off", "0", "false"}


def _local_panel_identity() -> tuple:
    from pathlib import Path

    from finaince.runtime import raw_local_data_root

    root = raw_local_data_root()
    signature = ("", -1, -1)
    if root:
        probe = Path(root)
        if probe.is_dir():
            probe = probe / "prices.parquet"
        try:
            st = probe.stat()
            signature = (str(probe), st.st_mtime_ns, st.st_size)
        except OSError:
            signature = (str(probe), -1, -1)
    return signature


def _install_panel_cache() -> bool:
    """Wrap DataLoader.load_price_data with a process-level local-panel cache (WS-C).

    reproagent's build_backtest_bundle reloads the parquet on every eval; within
    one batch loop the panel is identical, so we memoize the local branch only.
    Ricequant/qlib/tushare sources stay uncached (live fetches may vary).
    """
    global _PANEL_CACHE_INSTALLED
    if _PANEL_CACHE_INSTALLED:
        return True
    try:
        from reproagent.reproducer.data_loader import DataLoader
    except Exception:
        return False

    original = DataLoader.load_price_data

    def cached_load(self, universe, start, end):
        if _panel_cache_disabled() or self.settings.data_source != "local":
            return original(self, universe, start, end)
        key = (
            self.settings.data_source,
            str(universe),
            getattr(start, "isoformat", lambda: str(start))(),
            getattr(end, "isoformat", lambda: str(end))(),
            _local_panel_identity(),
        )
        hit = _PANEL_CACHE.get(key)
        if hit is not None:
            _PANEL_CACHE_STATS["hits"] += 1
            return hit.clone()
        _PANEL_CACHE_STATS["misses"] += 1
        frame = original(self, universe, start, end)
        if len(_PANEL_CACHE) >= 8:
            _PANEL_CACHE.clear()
        _PANEL_CACHE[key] = frame.clone()
        return frame

    DataLoader.load_price_data = cached_load
    _PANEL_CACHE_INSTALLED = True
    return True


def listed_operators() -> set[str]:
    import re
    from pathlib import Path

    global _OPERATORS, _OPERATORS_MTIME
    path = Path(__file__).with_name("operators.yaml")
    if not path.exists():
        return {"Rank", "Ref", "Delta", "Mean", "Std", "Corr"}
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _OPERATORS is not None and mtime == _OPERATORS_MTIME:
        return _OPERATORS
    names: set[str] = set()
    for match in re.finditer(r"(?:name|aliases|maps_to):\s*\[?([A-Za-z0-9_, ]+)\]?", path.read_text()):
        for part in match.group(1).split(","):
            token = part.strip()
            if token:
                names.add(token)
    _OPERATORS = names or {"Rank", "Ref", "Delta", "Mean", "Std", "Corr"}
    _OPERATORS_MTIME = mtime
    return _OPERATORS


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

        from reproagent.reproducer.backtest_bundle import build_backtest_bundle

        from finaince.domain.factor import finite_ic
        from finaince.runtime import default_backtest_window, packaged_local_panel
        from finaince.settings import reproagent_runtime_settings

        _install_panel_cache()

        if req.start and req.end and req.start > req.end:
            emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=False)
            return EvalResult(
                ok=False,
                dialect=req.dialect,
                data_backend=req.data_backend,
                error="invalid_window",
                translatable=translatable,
                alt_text=alt_text,
            )
        if req.cost_bps is not None:
            cost = finite_ic(req.cost_bps)
            if cost is None or cost < 0 or cost > 10_000:
                emit("eval_finished", dialect=req.dialect, data_backend=req.data_backend, ok=False)
                return EvalResult(
                    ok=False,
                    dialect=req.dialect,
                    data_backend=req.data_backend,
                    error="invalid_cost_bps",
                    translatable=translatable,
                    alt_text=alt_text,
                )

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
            from finaince.review.gates import _claims_broad_universe

            if _claims_broad_universe(universe):
                universe = "local_panel"
        cost_bps = req.cost_bps
        bundle_kwargs: dict[str, Any] = {
            "start_date": start,
            "end_date": end,
            "universe": universe,
            "settings": settings,
            "transaction_cost_bps": 0.0,
        }
        try:
            bt = build_backtest_bundle(req.expression, **bundle_kwargs)
        except Exception as exc:  # noqa: BLE001
            emit("eval_finished", dialect=req.dialect, data_backend=backend, ok=False)
            return EvalResult(
                ok=False,
                dialect=req.dialect,
                data_backend=backend or settings.data_source,
                error=f"backtest_failed:{exc}",
                translatable=translatable,
                alt_text=alt_text,
                warnings=warnings,
            )
        rows = bt.get("rows") or 0
        ic = finite_ic(bt.get("ic_mean"))
        ok = ic is not None and int(rows) > 0
        daily_returns: dict[str, float] = {}
        turnover_series: dict[str, float] = {}
        equity_path = bt.get("equity_curve_path")
        if equity_path:
            try:
                import polars as pl

                df = pl.read_parquet(str(equity_path))
                if "date" in df.columns and "ls_return_raw" in df.columns:
                    for d, r in zip(df["date"].to_list(), df["ls_return_raw"].to_list()):
                        if r is not None:
                            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
                            daily_returns[key] = float(r)
                if "date" in df.columns and "turnover" in df.columns:
                    for d, t in zip(df["date"].to_list(), df["turnover"].to_list()):
                        if t is not None:
                            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
                            turnover_series[key] = float(t)
            except Exception:
                daily_returns = {}
                turnover_series = {}

        sharpe_ratio = bt.get("sharpe_ratio")
        max_drawdown = bt.get("max_drawdown")
        long_short_annual_return = bt.get("long_short_annual_return")
        turnover_mean = None
        sharpe_net = None

        if cost_bps > 0:
            if not turnover_series:
                warnings.append("cost_not_applied_no_turnover")
            else:
                import polars as pl
                from reproagent.reproducer.metrics import compute_max_drawdown, compute_sharpe
                
                dates = sorted(daily_returns.keys())
                net_returns = []
                for d in dates:
                    raw = daily_returns[d]
                    t = turnover_series.get(d, 0.0)
                    net = raw - (cost_bps / 10000.0) * t
                    net_returns.append(net)
                    daily_returns[d] = net
                
                if net_returns:
                    net_series = pl.Series("net_return", net_returns)
                    sharpe_net = compute_sharpe(net_series)
                    equity_curve = (1 + net_series).cum_prod()
                    max_drawdown = compute_max_drawdown(equity_curve)
                    long_short_annual_return = float(net_series.mean() or 0.0) * 252
                    sharpe_ratio = sharpe_net
                    turnover_mean = sum(turnover_series.values()) / len(turnover_series)

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
        
        metrics_dict = {
            "validation": payload,
            "ic_mean": ic,
            "ic_ir": bt.get("ic_ir"),
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "long_short_annual_return": long_short_annual_return,
            "backtest_id": bt.get("backtest_id"),
            "rows": rows,
            "start": start,
            "end": end,
            "data_source": settings.data_source,
            "universe_claim": universe,
            "alt_text": alt_text,
            "daily_returns": daily_returns,
            "transaction_cost_bps": cost_bps,
        }
        if cost_bps > 0:
            metrics_dict["cost_bps"] = cost_bps
            if turnover_mean is not None:
                metrics_dict["turnover_mean"] = turnover_mean
            if sharpe_net is not None:
                metrics_dict["sharpe_net"] = sharpe_net

        return EvalResult(
            ok=ok,
            dialect=req.dialect,
            data_backend=backend or settings.data_source,
            metrics=metrics_dict,
            translatable=translatable,
            alt_text=alt_text,
            warnings=warnings,
            error=None if ok else "empty_or_missing_ic",
        )
    if req.dialect == "qlib":
        from finaince.eval.qlib_subprocess import (
            child_qlib_eval,
            qlib_subprocess_enabled,
            run_qlib_eval,
        )
        from finaince.runtime import packaged_local_panel, qlib_local_data_path

        backend = (req.data_backend or "local").strip().lower()
        if backend in {"", "auto"}:
            backend = "local"
        path = qlib_local_data_path() or packaged_local_panel()
        if qlib_subprocess_enabled():
            payload = run_qlib_eval(
                req.expression,
                start=req.start,
                end=req.end,
                universe=req.universe,
                data_backend=backend,
                local_data_path=str(path) if path else None,
            )
            via = "qlib_subprocess"
        else:
            payload = child_qlib_eval(
                {
                    "expression": req.expression,
                    "data_backend": "local",
                    "local_data_path": str(path) if path else None,
                    "local_data_layout": "panel",
                    "start": req.start or "2023-01-02",
                    "end": req.end or "2023-02-10",
                }
            )
            via = "qlib_child"
        ok = bool(payload.get("ok"))
        emit("eval_finished", dialect=req.dialect, data_backend=backend, ok=ok)
        return EvalResult(
            ok=ok,
            dialect=req.dialect,
            data_backend=backend,
            metrics={
                **(payload.get("metrics") or {}),
                "via": via,
                "universe_claim": req.universe,
                "alt_text": alt_text,
            },
            translatable=translatable,
            alt_text=alt_text,
            error=None if ok else str(payload.get("error") or "qlib_child_failed"),
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

"""Factor / model joint loop. Bandit-style: alternate, optimize a portfolio metric."""

from __future__ import annotations

from typing import Any


def choose_next_action(history: list[dict[str, Any]] | None = None) -> str:
    """Pick factor vs model from prior portfolio metric / skip, not a blind flip."""
    rows = list(history or [])
    if not rows:
        return "factor"
    last = rows[-1]
    action = str(last.get("action") or last.get("chosen") or "")
    metrics = dict(last.get("metrics") or {})
    skip = last.get("skip_reason") or last.get("reason") or metrics.get("reason")
    port = metrics.get("portfolio_return")
    has_returns = bool(
        metrics.get("return_points")
        or metrics.get("rows")
        or last.get("daily_returns")
    )
    if action == "factor":
        if last.get("ok") and has_returns:
            return "model"
        return "factor"
    if action == "model":
        if skip or port is None:
            return "factor"
        try:
            if float(port) <= 0:
                return "factor"
        except (TypeError, ValueError):
            return "factor"
        return "model"
    return "factor"


def _equity_curve(returns: dict[str, float]) -> dict[str, float]:
    nav = 1.0
    curve: dict[str, float] = {}
    for key in sorted(returns):
        try:
            nav *= 1.0 + float(returns[key])
        except (TypeError, ValueError):
            continue
        curve[str(key)] = nav
    return curve


def _portfolio_return(curve: dict[str, float]) -> float | None:
    if len(curve) < 2:
        return None
    keys = sorted(curve)
    start = float(curve[keys[0]])
    end = float(curve[keys[-1]])
    if start == 0:
        return None
    return end / start - 1.0


def run_factor_step() -> dict[str, Any]:
    from finaince.eval.router import EvalRequest, evaluate

    result = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    returns = {}
    if isinstance(result.metrics, dict):
        raw = result.metrics.get("daily_returns") or {}
        if isinstance(raw, dict):
            returns = {str(k): float(v) for k, v in raw.items() if _is_num(v)}
    return {
        "action": "factor",
        "ok": bool(result.ok),
        "error": result.error,
        "expression": "Rank(Delta(close, 1))",
        "metrics": {
            "ic_mean": (result.metrics or {}).get("ic_mean"),
            "sharpe_ratio": (result.metrics or {}).get("sharpe_ratio"),
        },
        "daily_returns": returns,
        "factor_set": [{"expression": "Rank(Delta(close, 1))", "ok": result.ok}],
    }


def run_model_step(factor_step: dict[str, Any] | None = None) -> dict[str, Any]:
    """Train a thin linear head on factor returns, or skip honestly."""
    returns = dict((factor_step or {}).get("daily_returns") or {})
    trained = train_linear_head(returns)
    if trained.get("skipped"):
        return {
            "action": "model",
            "ok": False,
            "skipped": True,
            "reason": trained.get("reason"),
            "model_config": trained.get("model_config") or {"kind": "ols_linear"},
            "equity_curve": {},
        }
    curve = dict(trained.get("equity_curve") or {})
    port = trained.get("portfolio_return")
    if port is None:
        port = _portfolio_return(curve)
    return {
        "action": "model",
        "ok": port is not None,
        "skipped": False,
        "model_config": trained.get("model_config"),
        "equity_curve": curve,
        "metrics": {"portfolio_return": port, "points": len(curve)},
    }


def train_linear_head(returns: dict[str, float]) -> dict[str, Any]:
    """OLS on lag-1/lag-2 factor returns. No sklearn required; skip if too thin."""
    config: dict[str, Any] = {"kind": "ols_linear", "lags": 2, "backend": "local_panel"}
    series = [(k, float(v)) for k, v in sorted(returns.items()) if _is_num(v)]
    if len(series) < 6:
        return {"skipped": True, "reason": "too_few_rows", "model_config": config}
    try:
        import numpy as np
    except Exception:
        return {"skipped": True, "reason": "numpy_unavailable", "model_config": config}
    vals = np.array([v for _, v in series], dtype=float)
    y = vals[2:]
    x = np.column_stack([np.ones(len(y)), vals[1:-1], vals[:-2]])
    try:
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    except Exception as exc:  # noqa: BLE001
        return {"skipped": True, "reason": f"ols_failed:{exc}", "model_config": config}
    pred = x @ coef
    pnl = pred * y
    curve_vals = np.cumprod(1.0 + pnl)
    keys = [k for k, _ in series[2:]]
    curve = {str(k): float(v) for k, v in zip(keys, curve_vals, strict=False)}
    config = {
        **config,
        "coef": [float(c) for c in coef],
        "n_obs": int(len(y)),
    }
    port = _portfolio_return(curve)
    return {
        "skipped": False,
        "model_config": config,
        "equity_curve": curve,
        "portfolio_return": port,
    }


def run_loop(*, steps: int = 2) -> dict[str, Any]:
    """Two-step default: factor then model (or reverse if history already has factor)."""
    from finaince.trace import append_event, list_chain

    history = list(reversed(list_chain(limit=20)))
    chosen: list[str] = []
    factor_set: list[dict[str, Any]] = []
    model_config: dict[str, Any] | None = None
    equity_curve: dict[str, float] = {}
    last_factor: dict[str, Any] | None = None
    degraded: str | None = None
    n = max(2, int(steps))
    for _ in range(n):
        action = choose_next_action(history)
        chosen.append(action)
        if action == "factor":
            step = run_factor_step()
            last_factor = step
            factor_set = list(step.get("factor_set") or [])
            if not step.get("ok"):
                degraded = str(step.get("error") or "factor_step_failed")
            factor_metrics = dict(step.get("metrics") or {})
            if step.get("daily_returns"):
                factor_metrics["return_points"] = len(step["daily_returns"])
            ev = append_event(
                "loop_factor",
                metrics=factor_metrics,
                error=step.get("error"),
                extra={"action": "factor"},
            )
            history.append(
                {
                    "action": "factor",
                    "id": ev.get("id"),
                    "ok": step.get("ok"),
                    "metrics": factor_metrics,
                    "daily_returns": step.get("daily_returns"),
                }
            )
        else:
            step = run_model_step(last_factor)
            model_config = dict(step.get("model_config") or {})
            equity_curve = dict(step.get("equity_curve") or {})
            if step.get("skipped"):
                degraded = str(step.get("reason") or "model_skipped")
            model_metrics = dict(step.get("metrics") or {})
            if step.get("reason"):
                model_metrics.setdefault("reason", step.get("reason"))
            ev = append_event(
                "loop_model",
                metrics=model_metrics,
                extra={"action": "model"},
            )
            history.append(
                {
                    "action": "model",
                    "id": ev.get("id"),
                    "ok": step.get("ok"),
                    "metrics": model_metrics,
                    "skip_reason": step.get("reason") if step.get("skipped") else None,
                }
            )
    has_both = "factor" in chosen and "model" in chosen
    if not has_both:
        degraded = degraded or "missing_action"
    return {
        "ok": has_both and (bool(equity_curve) or bool(degraded)),
        "actions": chosen,
        "factor_set": factor_set,
        "model_config": model_config,
        "equity_curve": equity_curve,
        "degraded": degraded,
        "skip_reason": degraded if not equity_curve else None,
    }


def _is_num(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True

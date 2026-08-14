"""Factor / model joint loop. Bandit-style: alternate, optimize a portfolio metric."""

from __future__ import annotations

from typing import Any


def choose_next_action(history: list[dict[str, Any]] | None = None) -> str:
    """Heuristic bandit: start with factor; flip after each recorded action."""
    rows = list(history or [])
    if not rows:
        return "factor"
    last = str(rows[-1].get("action") or rows[-1].get("chosen") or "")
    if last == "factor":
        return "model"
    if last == "model":
        return "factor"
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
    """Thin long-short head: cumulate last factor daily returns. Honest skip if none."""
    returns = dict((factor_step or {}).get("daily_returns") or {})
    config = {"kind": "equal_weight_ls", "lookback": None, "backend": "local_panel"}
    if len(returns) < 3:
        return {
            "action": "model",
            "ok": False,
            "skipped": True,
            "reason": "no_factor_returns",
            "model_config": config,
            "equity_curve": {},
        }
    curve = _equity_curve(returns)
    port = _portfolio_return(curve)
    return {
        "action": "model",
        "ok": port is not None,
        "skipped": False,
        "model_config": config,
        "equity_curve": curve,
        "metrics": {"portfolio_return": port, "points": len(curve)},
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
            ev = append_event(
                "loop_factor",
                metrics=step.get("metrics") or {},
                error=step.get("error"),
                extra={"action": "factor"},
            )
            history.append({"action": "factor", "id": ev.get("id")})
        else:
            step = run_model_step(last_factor)
            model_config = dict(step.get("model_config") or {})
            equity_curve = dict(step.get("equity_curve") or {})
            if step.get("skipped"):
                degraded = str(step.get("reason") or "model_skipped")
            ev = append_event(
                "loop_model",
                metrics=step.get("metrics") or {"skipped": step.get("skipped"), "reason": step.get("reason")},
                extra={"action": "model"},
            )
            history.append({"action": "model", "id": ev.get("id")})
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

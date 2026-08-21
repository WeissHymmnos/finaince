"""Factor / model joint loop. Bandit-style: alternate, optimize a portfolio metric."""

from __future__ import annotations

import json
import os
from typing import Any


def choose_next_action(history: list[dict[str, Any]] | None = None) -> str:
    """Pick factor vs model from prior portfolio metric / skip, not a blind flip.

    WS-I: when a net combo Sharpe is present it is the reward signal; the raw
    portfolio_return sign stays the fallback for legacy histories.
    """
    rows = list(history or [])
    if not rows:
        return "factor"
    last = rows[-1]
    action = str(last.get("action") or last.get("chosen") or "")
    metrics = dict(last.get("metrics") or {})
    skip = last.get("skip_reason") or last.get("reason") or metrics.get("reason")
    port = metrics.get("portfolio_return")
    sharpe_net = metrics.get("sharpe_net")
    reward = sharpe_net if isinstance(sharpe_net, (int, float)) else port
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
        if skip or (port is None and sharpe_net is None):
            return "factor"
        try:
            if float(reward) <= 0:
                return "factor"
        except (TypeError, ValueError):
            return "factor"
        return "model"
    return "factor"


def advise_action(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the next action; FINAINCE_LOOP_ADVISOR=1 asks a chat LLM first.

    The heuristic answer is always computed and is the fallback whenever the
    advisor is off, unconfigured, or fails. Never raises.
    """
    heuristic_action = choose_next_action(history)
    if heuristic_action == "factor":
        if not history:
            heuristic_hyp = "factor step: Rank(Delta(close, 1)); chosen because first step"
        else:
            last = history[-1]
            if last.get("action") == "model":
                if last.get("skip_reason"):
                    reason = last.get("skip_reason")
                    heuristic_hyp = (
                        "factor step: Rank(Delta(close, 1)); "
                        f"chosen because previous model skipped ({reason})"
                    )
                else:
                    port = (last.get("metrics") or {}).get("portfolio_return")
                    heuristic_hyp = (
                        "factor step: Rank(Delta(close, 1)); "
                        f"chosen because portfolio_return {port} <= 0"
                    )
            else:
                heuristic_hyp = (
                    "factor step: Rank(Delta(close, 1)); "
                    "chosen because previous factor failed or had no returns"
                )
    else:
        heuristic_hyp = (
            "model step: ols_linear head on lag-1/lag-2 factor returns; "
            "chosen because previous factor succeeded with returns"
        )

    heuristic_result = {
        "action": heuristic_action,
        "hypothesis": heuristic_hyp,
        "via": "heuristic",
    }

    if os.environ.get("FINAINCE_LOOP_ADVISOR") != "1":
        return heuristic_result

    try:
        from finaince.runtime import resolve_llm

        llm = resolve_llm()
        base_url = str((llm or {}).get("base_url") or "")
        model = str((llm or {}).get("model") or "")
        api_key = (llm or {}).get("api_key")
        if not api_key or not base_url or not model:
            return {**heuristic_result, "advisor_error": "incomplete_provider"}

        import httpx

        prompt = "You are an advisor for a quantitative research loop. Choose the next action: 'factor' or 'model'.\n"
        prompt += "Provide a JSON response with 'action' and 'hypothesis' keys.\n"
        
        try:
            from finaince import coaching
            ctx = coaching.research_context()
            if ctx.get("ok"):
                samples = ctx.get("samples") or []
                lessons = ctx.get("lessons") or []
                if samples or lessons:
                    prompt += "Research Context:\n"
                    for s in samples[:3]:
                        expr = s.get("expression", "")
                        ic = s.get("ic", "")
                        prompt += f"known diverse factors: {expr} (ic={ic})\n"
                    for lesson in lessons[:3]:
                        err = lesson.get("error_head", "")
                        summ = lesson.get("summary_short", "")
                        prompt += f"recent failure: {err} — {summ}\n"
            try:
                from finaince.process_memory import chains_display

                for chain in chains_display(limit=2):
                    prompt += (
                        f"chain {chain['chain']}: head={chain.get('head')} tail={chain.get('tail')} "
                        f"best_rank_ic={chain.get('best_rank_ic')}\n"
                    )
            except Exception:
                pass
            try:
                from finaince.process_memory import context_block

                block = context_block(limit=3)
                if block:
                    prompt += block + "\n"
            except Exception:
                pass
            prompt += "\n"
        except Exception:
            pass

        prompt += "History of last events:\n"
        for ev in history[-10:]:
            prompt += json.dumps({
                "action": ev.get("action"),
                "ok": ev.get("ok"),
                "metrics": ev.get("metrics"),
                "skip_reason": ev.get("skip_reason"),
                "hypothesis": ev.get("hypothesis"),
            }) + "\n"

        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["choices"][0]["message"]["content"])

        action = parsed.get("action")
        if action not in ("factor", "model"):
            return {**heuristic_result, "advisor_error": f"invalid_action:{action}"}

        return {
            "action": action,
            "hypothesis": str(parsed.get("hypothesis") or ""),
            "via": "llm",
        }
    except Exception as exc:  # noqa: BLE001
        return {**heuristic_result, "advisor_error": str(exc)}


def _equity_curve(returns: dict[str, float]) -> dict[str, float]:
    from finaince.domain.scoring import equity_curve

    return equity_curve(returns)


def _portfolio_return(curve: dict[str, float]) -> float | None:
    if len(curve) < 2:
        return None
    keys = sorted(curve)
    start = float(curve[keys[0]])
    end = float(curve[keys[-1]])
    if start == 0:
        return None
    return end / start - 1.0


def run_factor_step(expression: str = "Rank(Delta(close, 1))") -> dict[str, Any]:
    from finaince.eval.router import EvalRequest, evaluate

    result = evaluate(EvalRequest(expression=expression, dialect="repro_polars"))
    returns = {}
    if isinstance(result.metrics, dict):
        raw = result.metrics.get("daily_returns") or {}
        if isinstance(raw, dict):
            returns = {str(k): float(v) for k, v in raw.items() if _is_num(v)}
    return {
        "action": "factor",
        "ok": bool(result.ok),
        "error": result.error,
        "expression": expression,
        "metrics": {
            "ic_mean": (result.metrics or {}).get("ic_mean"),
            "sharpe_ratio": (result.metrics or {}).get("sharpe_ratio"),
        },
        "daily_returns": returns,
        "factor_set": [{"expression": expression, "ok": result.ok}],
    }


def run_model_step(factor_step: dict[str, Any] | None = None) -> dict[str, Any]:
    """Thin head on factor returns, plus WS-I cross-factor dynamic combination."""
    from finaince import model_head

    returns = dict((factor_step or {}).get("daily_returns") or {})
    trained = model_head.train_head(returns)
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

    combination_payload: dict[str, Any] | None = None
    try:
        from finaince import combination

        combo = combination.try_combine_ready(min_factors=2)
        if combo.get("ok"):
            sharpe_net = combo.get("sharpe_net")
            if isinstance(sharpe_net, (int, float)) and sharpe_net > 0:
                port = float(sharpe_net)
            combination_payload = {
                "members": combo.get("members"),
                "sharpe_net": combo.get("sharpe_net"),
                "mean_turnover": combo.get("mean_turnover"),
                "n_days": combo.get("n_days"),
            }
        else:
            combination_payload = {"skipped": True, "reason": combo.get("reason")}
    except Exception as exc:  # noqa: BLE001
        combination_payload = {"skipped": True, "reason": f"combination_error:{exc}"}

    metrics: dict[str, Any] = {"portfolio_return": port, "points": len(curve)}
    if combination_payload and not combination_payload.get("skipped"):
        metrics["sharpe_net"] = combination_payload.get("sharpe_net")
    result = {
        "action": "model",
        "ok": port is not None,
        "skipped": False,
        "model_config": trained.get("model_config"),
        "equity_curve": curve,
        "metrics": metrics,
    }
    if combination_payload is not None:
        result["combination"] = combination_payload
    return result


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


def run_loop(*, steps: int = 2, expressions: list[str] | None = None) -> dict[str, Any]:
    """Two-step default: factor then model (or reverse if history already has factor)."""
    from finaince.trace import append_event, list_chain

    history = list(reversed(list_chain(limit=20)))
    chosen: list[str] = []
    factor_set: list[dict[str, Any]] = []
    model_config: dict[str, Any] | None = None
    equity_curve: dict[str, float] = {}
    last_factor: dict[str, Any] | None = None
    degraded: str | None = None
    expressions_evaluated: list[dict[str, Any]] = []
    
    queue = list(expressions) if expressions is not None else ["Rank(Delta(close, 1))"]
    
    n = max(2, int(steps))
    for _ in range(n):
        advice = advise_action(history)
        action = advice["action"]
        hypothesis = advice["hypothesis"]
        via = advice["via"]

        if action == "factor" and not queue:
            degraded = "expression_queue_empty"
            break

        chosen.append(action)
        if action == "factor":
            expr = queue.pop(0)
            step = run_factor_step(expr)
            last_factor = step
            factor_set = list(step.get("factor_set") or [])
            if not step.get("ok"):
                degraded = str(step.get("error") or "factor_step_failed")
            factor_metrics = dict(step.get("metrics") or {})
            if step.get("daily_returns"):
                factor_metrics["return_points"] = len(step["daily_returns"])
            
            factor_metrics["expression"] = expr
            hypothesis = f"{hypothesis} (expression: {expr})"
            
            expressions_evaluated.append({
                "expression": expr,
                "ok": step.get("ok"),
                "ic_mean": factor_metrics.get("ic_mean"),
            })

            extra = {"action": "factor", "via": via}
            if "advisor_error" in advice:
                extra["advisor_error"] = advice["advisor_error"]

            ev = append_event(
                "loop_factor",
                metrics=factor_metrics,
                error=step.get("error"),
                extra=extra,
                hypothesis=hypothesis,
            )
            history.append(
                {
                    "action": "factor",
                    "id": ev.get("id"),
                    "ok": step.get("ok"),
                    "metrics": factor_metrics,
                    "daily_returns": step.get("daily_returns"),
                    "hypothesis": hypothesis,
                }
            )
        else:
            step = run_model_step(last_factor)
            model_config = dict(step.get("model_config") or {})
            equity_curve = dict(step.get("equity_curve") or {})
            if step.get("skipped"):
                degraded = str(step.get("reason") or "model_skipped")
                hypothesis = f"{hypothesis} (skipped: {step.get('reason')})"
            model_metrics = dict(step.get("metrics") or {})
            if step.get("reason"):
                model_metrics.setdefault("reason", step.get("reason"))

            extra = {"action": "model", "via": via}
            if "advisor_error" in advice:
                extra["advisor_error"] = advice["advisor_error"]

            ev = append_event(
                "loop_model",
                metrics=model_metrics,
                extra=extra,
                hypothesis=hypothesis,
            )
            history.append(
                {
                    "action": "model",
                    "id": ev.get("id"),
                    "ok": step.get("ok"),
                    "metrics": model_metrics,
                    "skip_reason": step.get("reason") if step.get("skipped") else None,
                    "hypothesis": hypothesis,
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
        "expressions_evaluated": expressions_evaluated,
    }


def _is_num(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True

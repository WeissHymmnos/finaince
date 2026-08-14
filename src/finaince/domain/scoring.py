"""Named scorers: selection_score (pure) vs library_grade (backtest then 0-100)."""

from __future__ import annotations

from typing import Any


def selection_score(
    metrics: dict[str, Any],
    factor_ic: float = 0.0,
    walk_forward: dict[str, Any] | None = None,
) -> float:
    from aiminer.core.strategy import selection_score as shipped

    return float(shipped(metrics, factor_ic=factor_ic, walk_forward=walk_forward))


def library_grade(
    *,
    expression: str | None = None,
    backtest_id: str | None = None,
    ic_mean: float | None = None,
    sharpe: float | None = None,
    max_drawdown: float | None = None,
    dsr: float | None = None,
    pbo: float | None = None,
) -> dict[str, Any]:
    """0-100 + A/B/C/D. If expression is given, run the shipped FastMCP backtest path."""
    if expression:
        from reproagent.mcp_server import build_mcp_server

        # Drive the real scoring via the same math as FastMCP without swapping semantics.
        from finaince.tools import run_library_grade_backtest

        return run_library_grade_backtest(expression, backtest_id=backtest_id)

    score = 50.0
    if ic_mean is not None:
        score += max(-20.0, min(20.0, float(ic_mean) * 200.0))
    if sharpe is not None:
        score += max(-15.0, min(15.0, float(sharpe) * 5.0))
    if max_drawdown is not None:
        score -= max(0.0, min(15.0, abs(float(max_drawdown)) * 20.0))
    if pbo is not None:
        score -= max(0.0, min(20.0, float(pbo) * 25.0))
    score = max(0.0, min(100.0, score))
    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    else:
        grade = "D"
    return {"score": round(score, 1), "grade": grade, "backtest_id": backtest_id}


def named_score(scorer: str, **kwargs: Any) -> Any:
    if scorer == "selection_score":
        return {
            "scorer": "selection_score",
            "score": selection_score(
                dict(kwargs.get("metrics") or {}),
                factor_ic=float(kwargs.get("factor_ic") or 0.0),
                walk_forward=kwargs.get("walk_forward"),
            ),
        }
    if scorer == "library_grade":
        return {"scorer": "library_grade", **library_grade(**kwargs)}
    raise ValueError(f"unknown scorer {scorer!r}")

"""Named scorers (selection_score, library_grade) + the single source of return math.

Sharpe / max-drawdown / equity curve / IC t-stat live here and ONLY here.
Every module that reports performance numbers must import from this module so
that the benchmark table, the promotion gates and the loop reward can never
drift into different conventions.
"""

from __future__ import annotations

import math
from typing import Any

TRADING_DAYS = 252.0


def sharpe_ratio(values: list[float] | tuple[float, ...]) -> float | None:
    """Annualized Sharpe of a daily return series; None when undefined (<20 obs or zero variance)."""
    if len(values) < 20:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    std = math.sqrt(var) if var > 0 else None
    if std is None:
        return None
    return mean * math.sqrt(TRADING_DAYS) / std


def equity_curve(daily: dict[str, float]) -> dict[str, float]:
    """Cumulative NAV keyed by the input's own keys, sorted; non-numeric values skipped."""
    nav = 1.0
    curve: dict[str, float] = {}
    for key in sorted(daily):
        try:
            nav *= 1.0 + float(daily[key])
        except (TypeError, ValueError):
            continue
        curve[str(key)] = round(nav, 6)
    return curve


def max_drawdown(values: list[float] | tuple[float, ...]) -> float:
    """Most-negative peak-to-trough on the compounded series (<= 0.0 convention)."""
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    return drawdown


def ic_t_stat(ic_ir: float | None, n_days: int | None) -> float | None:
    """Harvey & Liu (2016) t-stat for IC: |ICIR| * sqrt(n_days)."""
    if ic_ir is None or n_days is None or n_days <= 0:
        return None
    return ic_ir * math.sqrt(n_days)


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

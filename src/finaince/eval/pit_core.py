"""One point-in-time scorer for bench, screening, and ``eval --backend pit``.

Signal on day t is a function of prices through t. The forward return is the
next close over this close, so day t's PnL is known on t+1. A window's last
day is dropped when embargo is on, because that forward return uses the next
window. Turnover is ``quintile_membership_churn`` (participation), not dollars.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

TURNOVER_MODEL = "quintile_membership_churn"

_RANK_DELTA = re.compile(
    r"^(?P<neg>-)?Rank\(Delta\((?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<n>\d+)\)\)$"
)


def parse_rank_delta(expression: str) -> tuple[str, int, bool]:
    match = _RANK_DELTA.match(expression.strip())
    if match is None:
        raise ValueError(f"unsupported_expression:{expression}")
    return match.group("field"), int(match.group("n")), bool(match.group("neg"))


def score_expression(frame: Any, expression: str) -> Any:
    """Cross-sectional ordinal rank of Delta(field, n). Negation ranks the negated delta."""
    import polars as pl

    field, window, negate = parse_rank_delta(expression)
    if field not in frame.columns:
        raise ValueError(f"missing_field:{field}")
    panel = frame.sort(["ts_code", "trade_date"])
    raw = pl.col(field) - pl.col(field).shift(window).over("ts_code")
    if negate:
        raw = -raw
    ranked = panel.with_columns(raw.alias("_raw")).with_columns(
        pl.col("_raw").rank(method="ordinal").over("trade_date").alias("_score")
    )
    return ranked.select(["trade_date", "ts_code", "_score"])


def metrics_for_window(
    scores: Any,
    forwards: Any,
    universe_by_day: dict[Any, set[str]],
    start: date,
    end: date,
    cost_bps: float,
    *,
    embargo: bool = True,
) -> dict[str, Any]:
    """IC / RankIC / net Sharpe for one window. Same function bench and sweep call."""
    from finaince.data_track import (
        _factor_artifacts,
        _metrics_from_artifacts,
        _scoped_universe,
    )

    scoped = _scoped_universe(universe_by_day, start, end, embargo=embargo)
    metrics = _metrics_from_artifacts(_factor_artifacts(scores, forwards, scoped), cost_bps)
    names: set[str] = set()
    for members in scoped.values():
        names.update(members)
    metrics["n_names"] = len(names)
    metrics["n_days"] = metrics.get("days")
    metrics["cost_bps"] = cost_bps
    metrics["turnover_model"] = TURNOVER_MODEL
    metrics["engine"] = "pit_core"
    metrics["window_start"] = start.isoformat()
    metrics["window_end"] = end.isoformat()
    metrics["embargo_last_day"] = embargo
    if "icir" not in metrics and "ic_ir" in metrics:
        metrics["icir"] = metrics["ic_ir"]
    return metrics


def score_frame_window(
    frame: Any,
    expression: str,
    universe_by_day: dict[Any, set[str]],
    start: date,
    end: date,
    cost_bps: float,
    *,
    embargo: bool = True,
) -> dict[str, Any]:
    from finaince.data_track import (
        _factor_artifacts,
        _forward_returns,
        _metrics_from_artifacts,
        _scoped_universe,
    )

    scores = score_expression(frame, expression)
    forwards = _forward_returns(frame)
    scoped = _scoped_universe(universe_by_day, start, end, embargo=embargo)
    artifacts = _factor_artifacts(scores, forwards, scoped)
    metrics = _metrics_from_artifacts(artifacts, cost_bps)

    names: set[str] = set()
    for members in scoped.values():
        names.update(members)
    metrics["n_names"] = len(names)
    metrics["n_days"] = metrics.get("days")
    metrics["cost_bps"] = cost_bps
    metrics["turnover_model"] = TURNOVER_MODEL
    metrics["engine"] = "pit_core"
    metrics["window_start"] = start.isoformat()
    metrics["window_end"] = end.isoformat()
    metrics["embargo_last_day"] = embargo
    if "icir" not in metrics and "ic_ir" in metrics:
        metrics["icir"] = metrics["ic_ir"]

    ls, turnover = artifacts["ls"]
    daily: dict[str, float] = {}
    for day, raw in ls:
        churn = turnover.get(day, 0.0)
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
        daily[key] = float(raw - (cost_bps / 10000.0) * churn)
    metrics["daily_returns"] = daily
    metrics["ic_mean"] = metrics.get("ic")
    metrics["sharpe_ratio"] = metrics.get("sharpe_net")
    metrics["citable"] = metrics.get("ic") is not None and int(metrics.get("n_names") or 0) >= 20
    return metrics


def _long_short(scores, forwards, universe_by_day, start, end, *, embargo: bool):
    from finaince.data_track import _factor_artifacts, _scoped_universe

    scoped = _scoped_universe(universe_by_day, start, end, embargo=embargo)
    arts = _factor_artifacts(scores, forwards, scoped)
    return arts["ls"]

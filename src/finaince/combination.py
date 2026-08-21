"""WS-I dynamic daily-weight combination across catalog-ready factors.

Anti-decay form (AlphaForge-style): every day each member gets a weight from
its trailing inverse volatility over ``lookback`` days (information up to t-1
only — no look-ahead), the combined long-short series pays double-sided cost on
weight churn. Thin/proxy members are down-weighted by simply being excluded
when their coverage is below the alignment floor.
"""

from __future__ import annotations

import math
from typing import Any

MIN_COVERAGE_RATIO = 0.6


def _series(returns_by_factor: dict[str, dict[str, float]], dates: list[str]) -> dict[str, list[float]]:
    table: dict[str, list[float]] = {}
    for fid, series in returns_by_factor.items():
        table[fid] = [float(series.get(day, 0.0)) for day in dates]
    return table


def _rolling_inverse_vol_weights(rows: dict[str, list[float]], *, lookback: int) -> list[dict[str, float]]:
    """Weight row per day computed from information strictly before that day."""
    factor_ids = sorted(rows)
    weights_history: list[dict[str, float]] = []
    for day_index in range(len(next(iter(rows.values())))):
        inv_vols: dict[str, float] = {}
        for fid in factor_ids:
            window = rows[fid][max(0, day_index - lookback) : day_index]
            if len(window) < 5:
                continue
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            vol = math.sqrt(var)
            if vol > 1e-12:
                inv_vols[fid] = 1.0 / vol
        total = sum(inv_vols.values())
        weights_history.append(
            {fid: v / total for fid, v in inv_vols.items()} if total > 0 else {}
        )
    return weights_history


def _turnover(prev_w: dict[str, float], curr_w: dict[str, float]) -> float:
    keys = set(prev_w) | set(curr_w)
    return sum(abs(curr_w.get(k, 0.0) - prev_w.get(k, 0.0)) for k in keys) / 2.0


def combine_daily(
    returns_by_factor: dict[str, dict[str, float]],
    *,
    lookback: int = 20,
    cost_bps: float = 5.0,
    min_factors: int = 2,
) -> dict[str, Any]:
    """Combine member series with daily inverse-vol weights; net-of-cost metrics."""
    if not returns_by_factor:
        return {"ok": False, "reason": "no_members"}
    common = None
    for series in returns_by_factor.values():
        keys = set(series)
        common = keys if common is None else (common & keys)
    common = common or set()
    all_dates = sorted({d for s in returns_by_factor.values() for d in s})
    covered_common = {
        day
        for day in all_dates
        if sum(1 for s in returns_by_factor.values() if day in s)
        >= max(min_factors, int(MIN_COVERAGE_RATIO * len(returns_by_factor)))
    }
    dates = [d for d in all_dates if d in covered_common] or all_dates
    if len(dates) < 30:
        return {"ok": False, "reason": "insufficient_overlap", "days": len(dates)}
    rows = _series(returns_by_factor, dates)
    weights_history = _rolling_inverse_vol_weights(rows, lookback=max(5, int(lookback)))

    combo: dict[str, float] = {}
    turnovers: list[float] = []
    prev_active: dict[str, float] | None = None
    for index, day in enumerate(dates):
        active = weights_history[index]
        gross = sum(w * rows[fid][index] for fid, w in active.items())
        if prev_active is not None and active:
            churn = _turnover(prev_active, active)
            turnovers.append(churn)
            gross -= (cost_bps / 10000.0) * churn * 2.0
        prev_active = active
        combo[day] = gross

    values = [combo[d] for d in dates]
    if len(values) < 20:
        return {"ok": False, "reason": "too_short"}
    from finaince.domain.scoring import max_drawdown, sharpe_ratio

    mean = sum(values) / len(values)
    sharpe = sharpe_ratio(values)
    drawdown = max_drawdown(values)
    ar = mean * 252.0
    return {
        "ok": True,
        "members": sorted(returns_by_factor),
        "n_days": len(values),
        "mean_turnover": round(sum(turnovers) / len(turnovers), 4) if turnovers else 0.0,
        "sharpe_net": round(sharpe, 4) if sharpe is not None else None,
        "annualized_return": round(ar, 6),
        "max_drawdown": round(drawdown, 4),
        "equity_curve": _equity(combo),
        "daily_returns": combo,
    }


def _equity(daily: dict[str, float]) -> dict[str, float]:
    from finaince.domain.scoring import equity_curve

    return equity_curve(daily)


def _series_sharpe(values: list[float]) -> float | None:
    from finaince.domain.scoring import sharpe_ratio

    return sharpe_ratio(values)


def combo_vs_best_member(
    returns_by_factor: dict[str, dict[str, float]],
    *,
    lookback: int = 20,
    cost_bps: float = 5.0,
) -> dict[str, Any]:
    """Acceptance judgment: does the combo beat every standalone member net?"""
    combo = combine_daily(returns_by_factor, lookback=lookback, cost_bps=cost_bps)
    if not combo.get("ok"):
        return {"ok": False, "reason": combo.get("reason")}
    standings = {}
    for fid, series in returns_by_factor.items():
        shared = [float(series.get(day, 0.0)) for day in combo["daily_returns"]]
        standings[fid] = _series_sharpe(shared)
    best_member = max((v for v in standings.values() if v is not None), default=None)
    verdict = {
        "ok": True,
        "combo_sharpe_net": combo.get("sharpe_net"),
        "best_member_sharpe": round(best_member, 4) if best_member is not None else None,
        "beats_best": bool(
            combo.get("sharpe_net") is not None
            and best_member is not None
            and combo["sharpe_net"] > best_member
        ),
        "member_sharpes": {k: (round(v, 4) if v is not None else None) for k, v in standings.items()},
    }
    return verdict


def try_combine_ready(*, min_factors: int = 2, limit: int = 50) -> dict[str, Any]:
    """Pull catalog ready factors and attempt a combination; honest skip otherwise."""
    try:
        from finaince.catalog.store import FactorCatalog

        records = [
            record
            for record in FactorCatalog().list(status="ready")[:limit]
            if record.daily_returns
        ]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"catalog_unavailable:{exc}"}
    if len(records) < min_factors:
        return {"ok": False, "reason": "insufficient_ready_factors", "n_ready_with_returns": len(records)}
    payload = {record.id: {str(k): float(v) for k, v in record.daily_returns.items()} for record in records}
    return combine_daily(payload)

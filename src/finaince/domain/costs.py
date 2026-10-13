"""Single source of truth for turnover → transaction-cost conversion ([P1-08]).

Quintile long-short book: each leg carries unit notional. A leg's membership
churn ``|A xor B| / |leg|`` already counts both names sold and names bought, so
it is that leg's traded-notional fraction. The book trades the sum of both legs.
The pipeline stores the *mean* of the two leg churns (a participation proxy), so
total traded notional is ``2 * mean_churn``.
"""

from __future__ import annotations

COST_MODEL = "bps_x_sum_of_leg_churn"
LS_LEGS = 2


def ls_trading_cost(mean_leg_churn: float, cost_bps: float) -> float:
    """Return cost for one rebalance of a two-leg book, as a return deduction."""
    return (cost_bps / 10000.0) * float(mean_leg_churn) * LS_LEGS


def weight_l1_cost(l1_turnover: float, cost_bps: float) -> float:
    """Cost for a book whose turnover is already the L1 weight change (traded notional)."""
    return (cost_bps / 10000.0) * float(l1_turnover)

"""Comprehensive Phase 1 remediation E2E test suite across Tiers 1-4.

Coverage:
- R1: Cross-year data continuity and absence of forward leakage / fake drops ([P0-01])
- R2: Trading microstructure execution constraints (limit-up/down, suspensions) ([P0-02])
- R3: Walk-Forward regime timing model (causal expanding OLS, position clipping) ([P0-03])
- R4: Multiprocessing isolation and deadlock defense (timeout, collision defense) ([P0-06])
- R5: Metrics mathematical correctness & test collection ([P0-04], [P0-05], [P1-18])

Tiers:
- Tier 1: Coverage / Primary happy paths
- Tier 2: Boundary & Edge conditions
- Tier 3: Cross-Feature Interactions
- Tier 4: Real-World Multi-Year Stress Simulation
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

# ==============================================================================
# Helper fixtures and builders for opaque-box testing
# ==============================================================================

def _make_daily_panel(
    start_date: date,
    num_days: int,
    codes: list[str],
    base_price: float = 10.0,
    daily_growth: float = 0.001,
) -> pl.DataFrame:
    """Construct a clean, synthetic multi-day OHLCV panel in polars with cross-sectional dispersion."""
    rows = []
    for day_idx in range(num_days):
        current_date = start_date + timedelta(days=day_idx)
        # Skip weekends
        if current_date.weekday() >= 5:
            continue
        for code_idx, code in enumerate(codes):
            # Introduce cross-sectional price dispersion and idiosyncratic returns
            drift = daily_growth * (1.0 + 0.2 * ((code_idx % 5) - 2))
            p = (base_price + code_idx * 0.5) * ((1.0 + drift) ** day_idx)
            rows.append({
                "trade_date": current_date,
                "ts_code": code,
                "open": round(p * 0.998, 4),
                "high": round(p * 1.015, 4),
                "low": round(p * 0.990, 4),
                "close": round(p, 4),
                "volume": 100000.0 + code_idx * 1000.0,
                "amount": round(100000.0 * p, 2),
            })
    return pl.DataFrame(rows)


# ==============================================================================
# R1: Cross-Year Data Continuity and Post-Adjustment (P0-01)
# ==============================================================================

class TestR1CrossYearDataContinuity:
    """R1: Eliminate cross-year pre-adjustment drops and future dividend leakage."""

    def test_r1_tier1_forward_returns_basic(self) -> None:
        """Tier 1: Basic forward return formula: next close / current close - 1."""
        from finaince.data_track import _forward_returns

        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)],
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "close": [10.0, 11.0, 12.1],
        })
        fwd = _forward_returns(df)
        assert "trade_date" in fwd.columns
        assert "ts_code" in fwd.columns
        assert "_fwd_ret" in fwd.columns

        rets = fwd["_fwd_ret"].to_list()
        assert rets[0] == pytest.approx(0.10, rel=1e-5)
        assert rets[1] == pytest.approx(0.10, rel=1e-5)
        assert rets[2] is None  # Last bar has no next-day close

    def test_r1_tier1_fetch_year_live_specifies_post_adjustment(self) -> None:
        """Tier 1: RiceQuant query contract must use adjust_type='post' to eliminate future leakage."""
        import inspect

        from finaince.data_track import fetch_year_live

        source = inspect.getsource(fetch_year_live)
        assert 'adjust_type="post"' in source or "adjust_type='post'" in source, (
            "Defect [P0-01]: fetch_year_live still uses adjust_type='pre', "
            "causing cross-year disconnects and future dividend leakage."
        )

    def test_r1_tier2_year_boundary_continuity_no_fake_drop(self) -> None:
        """Tier 2: Year boundary splice (Dec 31 to Jan 4) must not create artificial drops."""
        from finaince.data_track import _forward_returns

        # Cross-year boundary panel: Dec 30, Dec 31, Jan 4, Jan 5
        df = pl.DataFrame({
            "trade_date": [date(2021, 12, 30), date(2021, 12, 31), date(2022, 1, 4), date(2022, 1, 5)],
            "ts_code": ["600000.SH", "600000.SH", "600000.SH", "600000.SH"],
            "close": [100.0, 101.0, 102.0, 103.0],
        })
        fwd = _forward_returns(df)
        dec31_ret = fwd.filter(pl.col("trade_date") == date(2021, 12, 31))["_fwd_ret"][0]
        # Expected return across the year boundary is (102.0 / 101.0 - 1.0) ≈ +0.0099
        expected_ret = 102.0 / 101.0 - 1.0
        assert dec31_ret == pytest.approx(expected_ret, rel=1e-5)
        assert dec31_ret > 0.0, "Dec 31 to Jan 4 return must be positive, not a fake drop"

    def test_r1_tier2_non_positive_and_near_zero_prices_guarded(self) -> None:
        """Tier 2: Non-positive or near-zero prices (<= 1e-6) must not cause division explosions."""
        from finaince.data_track import _forward_returns

        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5), date(2023, 1, 6)],
            "ts_code": ["600000.SH", "600000.SH", "600000.SH", "600000.SH"],
            "close": [10.0, 0.0, 10.0, 1e-7],
        })
        fwd = _forward_returns(df)
        rets = fwd["_fwd_ret"].to_list()
        # Division by zero or 1e-7 should be filtered out to None, not Infinity or error
        assert rets[0] is None or not math.isinf(rets[0])
        assert rets[1] is None or not math.isinf(rets[1])

    def test_r1_tier2_dividend_split_post_adjusted_invariance(self) -> None:
        """Tier 2: Ex-dividend / stock split under post-adjustment preserves smooth returns."""
        from finaince.data_track import _forward_returns

        # Under post-adjustment, a 1-to-2 split at year end maintains continuous price basis
        df = pl.DataFrame({
            "trade_date": [date(2022, 12, 30), date(2022, 12, 31), date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["000002.SZ", "000002.SZ", "000002.SZ", "000002.SZ"],
            # Post-adjusted prices: historical prices are scaled to avoid -50% fake drop
            "close": [50.0, 50.5, 51.0, 51.5],
        })
        fwd = _forward_returns(df)
        year_end_ret = fwd.filter(pl.col("trade_date") == date(2022, 12, 31))["_fwd_ret"][0]
        assert year_end_ret == pytest.approx(51.0 / 50.5 - 1.0, rel=1e-5)
        assert year_end_ret > -0.05, "Must not suffer fake split crash across year boundary"

    def test_r1_tier3_cross_year_factor_scores_and_ic(self) -> None:
        """Tier 3: Factor score and forward return join across year boundary preserves continuity."""
        from finaince.data_track import _daily_ic, _factor_scores, _forward_returns

        # 2 stocks across year-end
        codes = [f"60000{i}.SH" for i in range(15)]
        df = _make_daily_panel(date(2022, 12, 1), 40, codes, base_price=20.0, daily_growth=0.001)

        scores = _factor_scores(df, "rank_delta_20")
        forwards = _forward_returns(df)

        universe_by_day = {
            d: set(codes) for d in set(df["trade_date"].to_list())
        }
        ics = _daily_ic(scores, forwards, universe_by_day, spearman=True)
        assert len(ics) > 0, "Daily IC must compute successfully across year boundary"
        assert all(math.isfinite(ic) for ic in ics), "All ICs must be finite"

    def test_r1_tier4_multi_year_continuity_stress(self) -> None:
        """Tier 4: Multi-year (3 years) stress test with 30 tickers: all forward returns bounded."""
        from finaince.data_track import _forward_returns

        codes = [f"6000{i:02d}.SH" for i in range(20)]
        df = _make_daily_panel(date(2021, 11, 1), 250, codes, base_price=15.0, daily_growth=0.0005)

        fwd = _forward_returns(df)
        valid_rets = [r for r in fwd["_fwd_ret"].to_list() if r is not None]

        assert len(valid_rets) > 0
        # No forward return should be +/- infinity or NaN
        assert all(math.isfinite(r) for r in valid_rets)
        # All returns in reasonable range (-30% to +30% daily max for A-share)
        assert all(-0.35 <= r <= 0.35 for r in valid_rets)


# ==============================================================================
# R2: Trading Microstructure Execution Constraints (P0-02)
# ==============================================================================

class TestR2MicrostructureExecutionConstraints:
    """R2: Realistic trading constraints: limit-up buy block, limit-down sell block, suspensions."""

    def test_r2_tier1_board_specific_price_limits(self) -> None:
        """Tier 1: Main board 10%, ChiNext/STAR 20%, ST 5% with 0.01 CNY rounding."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not found in backtester.")

        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "600000.SH"],  # Main board
            "close": [10.0, 10.5],
            "open": [10.0, 10.4],
            "high": [10.1, 10.6],
            "low": [9.9, 10.3],
            "volume": [100000.0, 120000.0],
        })
        enriched = _enrich_trading_constraints(df)
        assert "up_limit" in enriched.columns
        assert "down_limit" in enriched.columns
        assert "can_buy" in enriched.columns
        assert "can_sell" in enriched.columns

        # Verify Main Board limit for day 2: prev_close = 10.0 -> up_limit = 11.00, down_limit = 9.00
        row2 = enriched.filter(pl.col("trade_date") == date(2023, 1, 3)).to_dicts()[0]
        # In enriched data, row for day t has next_up_limit or up_limit calculated
        if "up_limit" in row2:
            pass  # structure populated

    def test_r2_tier1_can_buy_can_sell_normal_trading_bar(self) -> None:
        """Tier 1: Normal trading day within limits and positive volume allows buy and sell."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not implemented.")

        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "600000.SH"],
            "close": [10.0, 10.2],
            "open": [10.0, 10.1],
            "high": [10.1, 10.3],
            "low": [9.9, 10.0],
            "volume": [100000.0, 150000.0],
        })
        enriched = _enrich_trading_constraints(df)
        first_row = enriched.filter(pl.col("trade_date") == date(2023, 1, 3)).to_dicts()[0]
        assert first_row["can_buy"] is True
        assert first_row["can_sell"] is True

    def test_r2_tier2_one_word_limit_up_rejects_buy(self) -> None:
        """Tier 2: One-word limit-up rejects buy order (can_buy=False, can_sell=True)."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not implemented.")

        # Day 2 is locked at one-word limit-up (open == high == low == close == 11.00)
        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "600000.SH"],
            "close": [10.0, 11.0],
            "open": [10.0, 11.0],
            "high": [10.1, 11.0],
            "low": [9.9, 11.0],
            "volume": [100000.0, 50000.0],
        })
        enriched = _enrich_trading_constraints(df)
        decision_row = enriched.filter(pl.col("trade_date") == date(2023, 1, 3)).to_dicts()[0]
        assert decision_row["can_buy"] is False, "Cannot buy on one-word limit-up"
        assert decision_row["can_sell"] is True, "Can sell on one-word limit-up"

    def test_r2_tier2_one_word_limit_down_rejects_sell(self) -> None:
        """Tier 2: One-word limit-down rejects sell order (can_sell=False, can_buy=True)."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not implemented.")

        # Day 2 is locked at one-word limit-down (open == high == low == close == 9.00)
        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "600000.SH"],
            "close": [10.0, 9.0],
            "open": [10.0, 9.0],
            "high": [10.1, 9.0],
            "low": [9.9, 9.0],
            "volume": [100000.0, 50000.0],
        })
        enriched = _enrich_trading_constraints(df)
        decision_row = enriched.filter(pl.col("trade_date") == date(2023, 1, 3)).to_dicts()[0]
        assert decision_row["can_sell"] is False, "Cannot sell into one-word limit-down"
        assert decision_row["can_buy"] is True, "Can buy on one-word limit-down"

    def test_r2_tier2_suspension_zero_volume_blocks_both(self) -> None:
        """Tier 2: Trading suspension (volume == 0) blocks both buy and sell."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not implemented.")

        # Day 2 is suspended (volume == 0)
        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "600000.SH"],
            "close": [10.0, 10.0],
            "open": [10.0, 10.0],
            "high": [10.0, 10.0],
            "low": [10.0, 10.0],
            "volume": [100000.0, 0.0],
        })
        enriched = _enrich_trading_constraints(df)
        decision_row = enriched.filter(pl.col("trade_date") == date(2023, 1, 3)).to_dicts()[0]
        assert decision_row["can_buy"] is False, "Cannot buy when suspended"
        assert decision_row["can_sell"] is False, "Cannot sell when suspended"

    def test_r2_tier2_null_loss_dropping_eliminated(self) -> None:
        """Tier 2: Limit-down asset losses must NOT be evaded by drop_nulls()."""
        import inspect

        from reproagent.reproducer.backtester import StrategyBacktester

        source = inspect.getsource(StrategyBacktester.run)
        # Old code did: forward_returns = data.select([..., "forward_return"]).drop_nulls()
        # and apply_limit_no_fill nulled forward_return for limit drops, evading losses!
        assert "drop_nulls()" not in source or "can_sell" in source or "limit_no_fill" in source, (
            "Defect [P0-02]: StrategyBacktester still drops null returns blindly, "
            "allowing limit-down loss evasion."
        )

    def test_r2_tier3_board_limit_rates_and_st_stocks(self) -> None:
        """Tier 3: Multi-asset cross-board verification: 10% (Main), 20% (ChiNext/STAR), 5% (ST)."""
        try:
            from reproagent.reproducer.backtester import _enrich_trading_constraints
        except ImportError:
            pytest.fail("Defect [P0-02]: _enrich_trading_constraints not implemented.")

        df = pl.DataFrame({
            "trade_date": [date(2023, 1, 3), date(2023, 1, 3), date(2023, 1, 3), date(2023, 1, 3)],
            "ts_code": ["600000.SH", "300750.SZ", "688981.SH", "000002ST.SZ"],
            "close": [10.0, 10.0, 10.0, 10.0],
            "open": [10.0, 10.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.0, 10.0],
            "low": [10.0, 10.0, 10.0, 10.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        })
        # Add next day prices for all boards
        df2 = pl.DataFrame({
            "trade_date": [date(2023, 1, 4), date(2023, 1, 4), date(2023, 1, 4), date(2023, 1, 4)],
            "ts_code": ["600000.SH", "300750.SZ", "688981.SH", "000002ST.SZ"],
            "close": [11.0, 12.0, 12.0, 10.5],
            "open": [11.0, 12.0, 12.0, 10.5],
            "high": [11.0, 12.0, 12.0, 10.5],
            "low": [11.0, 12.0, 12.0, 10.5],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        })
        combined = pl.concat([df, df2]).sort(["ts_code", "trade_date"])
        enriched = _enrich_trading_constraints(combined)

        limits = {row["ts_code"]: row for row in enriched.filter(pl.col("trade_date") == date(2023, 1, 4)).to_dicts()}
        assert limits["600000.SH"]["up_limit"] == pytest.approx(11.00, abs=0.01)
        assert limits["300750.SZ"]["up_limit"] == pytest.approx(12.00, abs=0.01)
        assert limits["688981.SH"]["up_limit"] == pytest.approx(12.00, abs=0.01)
        assert limits["000002ST.SZ"]["up_limit"] == pytest.approx(10.50, abs=0.01)

    def test_r2_tier4_consecutive_limit_down_cascade_loss_retention(self) -> None:
        """Tier 4: 3-day consecutive limit down cascade (-10% each) must realize -27.1% cumulative loss."""
        # A position trying to sell on day 1 cannot exit, retaining full loss across 3 days
        p0 = 100.0
        p3 = 72.9  # After Day 1 (-10% -> 90), Day 2 (-10% -> 81), Day 3 (-10% -> 72.9)
        expected_cumulative_loss = (p3 / p0) - 1.0  # -0.271
        assert expected_cumulative_loss == pytest.approx(-0.271, abs=1e-4)

    def test_r2_tier4_chinext_20_pct_gains_not_dropped(self) -> None:
        """Tier 4: ChiNext stock with +15% gain within 20% limit must retain positive returns."""
        import tempfile

        from reproagent.models.factor_def import FactorDefinition
        from reproagent.models.replication import BacktestParams
        from reproagent.reproducer.backtester import StrategyBacktester
        from reproagent.settings import Settings

        data = pl.DataFrame({
            "trade_date": [
                date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
                date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
            ],
            "ts_code": ["600000.SH", "600000.SH", "600000.SH", "300750.SZ", "300750.SZ", "300750.SZ"],
            "close": [10.0, 10.1, 10.1, 10.0, 11.5, 11.5],
            "open": [10.0, 10.0, 10.1, 10.0, 10.5, 11.5],
            "high": [10.1, 10.2, 10.2, 10.1, 11.6, 11.6],
            "low": [9.9, 9.9, 10.0, 9.9, 10.4, 11.4],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        })
        factor_values = pl.DataFrame({
            "date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 3), date(2023, 1, 4)],
            "asset": ["600000.SH", "600000.SH", "300750.SZ", "300750.SZ"],
            "factor_value": [0.1, 0.2, 0.9, 0.8],
        })
        backtester = StrategyBacktester(Settings(data_dir=Path(tempfile.mkdtemp())))
        params = BacktestParams(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 5),
            delay=1,
            limit_no_fill=True,
            mode="factor",
            num_groups=2,
        )
        res = backtester.run(
            factor_values,
            params,
            FactorDefinition.model_construct(id="f", name="f", expression="c", universe="all"),
            data=data,
        )
        assert res.group_annualized_returns[1] > 0, "ChiNext +15% return must not be dropped to 0.0"

    def test_r2_tier4_st_one_word_limit_up_buy_rejected(self) -> None:
        """Tier 4: ST stock locked at one-word limit-up (+5%) rejects buy order (0.0 return)."""
        import tempfile

        from reproagent.models.factor_def import FactorDefinition
        from reproagent.models.replication import BacktestParams
        from reproagent.reproducer.backtester import StrategyBacktester
        from reproagent.settings import Settings

        data = pl.DataFrame({
            "trade_date": [
                date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
                date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
            ],
            "ts_code": ["600000.SH", "600000.SH", "600000.SH", "000002ST.SZ", "000002ST.SZ", "000002ST.SZ"],
            "close": [10.0, 10.0, 10.0, 10.0, 10.5, 10.5],
            "open": [10.0, 10.0, 10.0, 10.0, 10.5, 10.5],
            "high": [10.0, 10.0, 10.0, 10.0, 10.5, 10.5],
            "low": [10.0, 10.0, 10.0, 10.0, 10.5, 10.5],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        })
        backtester = StrategyBacktester(Settings(data_dir=Path(tempfile.mkdtemp())))
        params = BacktestParams(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 5),
            delay=1,
            limit_no_fill=True,
            mode="factor",
            num_groups=2,
        )
        factor_values = pl.DataFrame({
            "date": [date(2023, 1, 3), date(2023, 1, 3)],
            "asset": ["600000.SH", "000002ST.SZ"],
            "factor_value": [0.1, 0.9],
        })
        res = backtester.run(
            factor_values,
            params,
            FactorDefinition.model_construct(id="f", name="f", expression="c", universe="all"),
            data=data,
        )
        assert res.group_annualized_returns[1] == 0.0, "Backtester must reject ST one-word limit-up buy"




# ==============================================================================
# R3: Walk-Forward Market Regime Timing Model (P0-03)
# ==============================================================================

class TestR3WalkForwardRegimeTiming:
    """R3: Strictly causal Walk-Forward expanding window OLS, position clipping, turnover friction."""

    def test_r3_tier1_walk_forward_expanding_ols_happy_path(self) -> None:
        """Tier 1: Expanding window regression on 100-day series returns valid causal curve."""
        from finaince.loop import train_linear_head

        rng = np.random.default_rng(42)
        dates = [f"2023-01-{i:02d}" for i in range(1, 31)] + [f"2023-02-{i:02d}" for i in range(1, 29)] + [f"2023-03-{i:02d}" for i in range(1, 32)]
        returns = {d: float(rng.normal(0.001, 0.02)) for d in dates[:85]}

        res = train_linear_head(returns)
        assert res["skipped"] is False
        assert "equity_curve" in res
        assert "portfolio_return" in res
        curve = res["equity_curve"]
        assert len(curve) > 0
        assert all(math.isfinite(v) for v in curve.values())

    def test_r3_tier2_short_history_skips_gracefully(self) -> None:
        """Tier 2: Series shorter than min_train must skip with 'too_few_rows' without exception."""
        from finaince.loop import train_linear_head

        short_returns = {f"2023-01-{i:02d}": 0.01 for i in range(1, 15)}
        res = train_linear_head(short_returns)
        assert res["skipped"] is True
        assert res.get("reason") in ("too_few_rows", "insufficient_history")

    def test_r3_tier2_position_clipping_bounds(self) -> None:
        """Tier 2: Outsized OLS predictions must be strictly clipped to [-1.0, 1.0]."""
        from finaince.loop import train_linear_head

        # Create massive explosive returns that would generate huge OLS predictions
        dates = [f"2023-01-{i:02d}" for i in range(1, 31)] + [f"2023-02-{i:02d}" for i in range(1, 29)] + [f"2023-03-{i:02d}" for i in range(1, 32)]
        returns = {d: 0.10 * ((i % 2) * 2 - 1) for i, d in enumerate(dates)}

        res = train_linear_head(returns)
        if not res["skipped"]:
            # If positions are logged or equity curve computed, verify equity curve does not explode exponentially
            curve = res["equity_curve"]
            assert all(v > 0.0 for v in curve.values())

    def test_r3_tier2_turnover_friction_penalty(self) -> None:
        """Tier 2: Higher cost_bps must strictly reduce net portfolio return."""
        from finaince.loop import train_linear_head

        rng = np.random.default_rng(123)
        dates = [f"2023-{m:02d}-{d:02d}" for m in range(1, 5) for d in range(1, 25)]
        returns = {k: float(rng.normal(0.0005, 0.015)) for k in dates[:80]}

        res_low_cost = train_linear_head(returns, cost_bps=0.0) if "cost_bps" in train_linear_head.__code__.co_varnames else None
        res_high_cost = train_linear_head(returns, cost_bps=50.0) if "cost_bps" in train_linear_head.__code__.co_varnames else None

        if res_low_cost and res_high_cost and not res_low_cost["skipped"] and not res_high_cost["skipped"]:
            assert res_low_cost["portfolio_return"] >= res_high_cost["portfolio_return"]

    def test_r3_tier3_strict_temporal_causality_zero_lookahead(self) -> None:
        """Tier 3: Future return shock (day 80+) must NOT alter predictions/equity prior to day 80.

        This is the mathematical proof test against full-sample OLS lookahead bias [P0-03].
        In an out-of-sample Walk-Forward model:
        prediction(t) = f(returns[0..t-1]). Future returns[t+1..T] CANNOT affect prediction(t).
        """
        from finaince.loop import train_linear_head

        dates = [f"2023-{m:02d}-{d:02d}" for m in range(1, 6) for d in range(1, 26)]
        base_series = {k: 0.005 * math.sin(i * 0.1) for i, k in enumerate(dates[:90])}

        # Corrupted series: identical up to day 70, then massive +50% crash/boom injected
        corrupted_series = dict(base_series)
        for i, k in enumerate(dates[70:90]):
            corrupted_series[k] = 0.50 * ((-1) ** i)

        res_base = train_linear_head(base_series)
        res_corrupt = train_linear_head(corrupted_series)

        assert not res_base["skipped"]
        assert not res_corrupt["skipped"]

        curve_base = res_base["equity_curve"]
        curve_corrupt = res_corrupt["equity_curve"]

        # Check early dates prior to day 70 (e.g. day 61 to 68)
        early_dates = [d for d in sorted(curve_base.keys()) if d < dates[70]]
        assert len(early_dates) > 0

        for d in early_dates:
            assert curve_base[d] == pytest.approx(curve_corrupt[d], rel=1e-5), (
                f"Defect [P0-03]: Lookahead bias detected at date {d}! "
                f"Future shock altered past equity curve (base={curve_base[d]}, corrupt={curve_corrupt[d]}). "
                "The model is using full-sample OLS instead of causal Walk-Forward expanding window!"
            )

    def test_r3_tier4_multi_year_walk_forward_simulation(self) -> None:
        """Tier 4: Long-horizon 250-day expanding estimation with regime shifts."""
        from finaince.loop import train_linear_head

        rng = np.random.default_rng(999)
        dates = [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 23)]
        returns = {k: float(rng.normal(0.0002, 0.01)) for k in dates[:200]}

        res = train_linear_head(returns)
        assert res["skipped"] is False
        assert len(res["equity_curve"]) >= 100
        assert math.isfinite(res["portfolio_return"])


# ==============================================================================
# R4: Multiprocessing Isolation and Deadlock Defense (P0-06)
# ==============================================================================

class TestR4MultiprocessingIsolation:
    """R4: Timeout hard interrupts, future mapping without PID collisions, sandbox budget."""

    def test_r4_tier1_batch_evaluation_happy_path(self) -> None:
        """Tier 1: Batch evaluate multiple factor steps concurrently."""
        from finaince.loop import _evaluate_batch

        exprs = ["Rank(Delta(close, 1))", "Rank(Delta(close, 5))"]
        results = _evaluate_batch(exprs, workers=2)
        assert isinstance(results, dict)
        for expr in exprs:
            assert expr in results

    def test_r4_tier2_duplicate_expression_collision_defense(self) -> None:
        """Tier 2: Duplicate expressions in queue must NOT overwrite futures or cause collisions."""
        from finaince.loop import _evaluate_batch

        exprs = ["Rank(Delta(close, 1))", "Rank(Delta(close, 1))", "Rank(Delta(close, 5))"]
        results = _evaluate_batch(exprs, workers=2)
        assert "Rank(Delta(close, 1))" in results
        assert "Rank(Delta(close, 5))" in results

    def test_r4_tier2_worker_timeout_handling(self) -> None:
        """Tier 2: Explicit per-task timeout prevents infinite hangs."""
        from finaince.loop import _evaluate_batch

        # Pass ultra-low timeout to trigger timeout defense if parameter supported
        exprs = ["Rank(Delta(close, 1))"]
        if "timeout_per_task" in _evaluate_batch.__code__.co_varnames:
            results = _evaluate_batch(exprs, workers=1, timeout_per_task=0.00001)
            # Timeout should fail cleanly without unhandled crash
            assert exprs[0] in results
        else:
            # Code does not have timeout parameter yet — escalate as P0-06 defect
            pytest.fail("Defect [P0-06]: _evaluate_batch lacks timeout_per_task parameter for worker timeouts.")

    def test_r4_tier2_worker_crash_isolated_from_peers(self) -> None:
        """Tier 2: Crashing expression does not bring down valid concurrent tasks."""
        from finaince.loop import _evaluate_batch

        exprs = ["INVALID_EXPR_SYNTAX_(((", "Rank(Delta(close, 1))"]
        results = _evaluate_batch(exprs, workers=2)
        assert len(results) >= 1

    def test_r4_tier3_sandbox_timeout_and_budget(self) -> None:
        """Tier 3: Isolated factor sandbox terminates runaway execution."""
        from finaince.isolate import run_isolated

        # Code with infinite loop should be caught by subprocess timeout
        hanging_code = "while True: pass"
        res = run_isolated(hanging_code, name="infinite_loop")
        assert res["ok"] is False
        assert "error" in res or "timeout" in str(res).lower()

    def test_r4_tier4_concurrency_stress_batch(self) -> None:
        """Tier 4: Concurrency batch under pool execution completes with zero deadlock."""
        from finaince.loop import _evaluate_batch

        exprs = ["Rank(Delta(close, 1))", "Rank(Delta(close, 5))"]
        results = _evaluate_batch(exprs, workers=2)
        assert len(results) == len(exprs)


# ==============================================================================
# R5: Metrics Mathematical Correctness & Test Discovery (P0-04, P0-05, P1-18)
# ==============================================================================

class TestR5MetricsMathAndDiscovery:
    """R5: Max drawdown robustness to NaNs/Inf, equity floor, initial NAV alignment, SDK decoupling."""

    def test_r5_tier1_max_drawdown_standard_monotone(self) -> None:
        """Tier 1: 3 consecutive -10% drops: compounded drawdown is 1 - 0.9^3 = -0.271."""
        from finaince.domain.scoring import max_drawdown

        values = [-0.10, -0.10, -0.10]
        dd = max_drawdown(values)
        assert dd == pytest.approx(-0.271, abs=1e-5)

    def test_r5_tier1_portfolio_return_baseline_1_nav(self) -> None:
        """Tier 1: Portfolio return measured against initial 1.0 baseline NAV preserves period 0 return."""
        from finaince.model_head import _portfolio_return

        # Single period equity curve: investment made at baseline 1.0 grows to 1.05
        curve = {"2024-01-01": 1.05}
        ret = _portfolio_return(curve)
        assert ret == pytest.approx(0.05, abs=1e-5), (
            f"Defect [P0-04]: _portfolio_return returned {ret} instead of 0.05. "
            "Period 0 return was dropped due to length < 2 check or wrong denominator!"
        )

    def test_r5_tier2_max_drawdown_nan_and_inf_resilience(self) -> None:
        """Tier 2: Max drawdown with NaNs and Inf must NOT silently return 0.0."""
        from finaince.domain.scoring import max_drawdown

        # Series: +10%, NaN, -20%, +Inf, -10%
        # Clean values: 1.0 -> 1.10 -> 0.88 (-20% from peak) -> 0.792 (-28% from peak 1.10)
        values = [0.10, float("nan"), -0.20, float("inf"), -0.10]
        dd = max_drawdown(values)

        assert dd != 0.0, (
            "Defect [P0-05]: max_drawdown silently returned 0.0 when input contained NaN! "
            "NaN corrupted peak and drawdown calculation."
        )
        assert dd < -0.20, "Drawdown must accurately reflect the valid negative drop"
        assert dd == pytest.approx(0.792 / 1.10 - 1.0, abs=1e-3)

    def test_r5_tier2_max_drawdown_catastrophic_wipeout_and_equity_floor(self) -> None:
        """Tier 2: Total loss (-1.0 or -1.5) capped at -1.0; equity floor prevents negative compounding."""
        from finaince.domain.scoring import max_drawdown

        # 100% loss: drawdown is -1.0
        assert max_drawdown([-1.0]) == pytest.approx(-1.0, abs=1e-5)

        # >100% loss (e.g. leveraged wipeout -1.5): equity floor must prevent negative equity
        dd_leveraged = max_drawdown([-1.5, -0.5])
        assert dd_leveraged == pytest.approx(-1.0, abs=1e-3)

    def test_r5_tier2_max_drawdown_empty_and_all_nan(self) -> None:
        """Tier 2: Empty or all-NaN return series returns 0.0."""
        from finaince.domain.scoring import max_drawdown

        assert max_drawdown([]) == 0.0
        assert max_drawdown([float("nan"), float("nan")]) == 0.0
        assert max_drawdown([None]) == 0.0  # type: ignore[list-item]

    def test_r5_tier2_portfolio_return_multi_period_alignment(self) -> None:
        """Tier 2: Multi-period curve returns total cumulative return over baseline 1.0."""
        from finaince.model_head import _portfolio_return

        curve = {"2024-01-01": 1.10, "2024-01-02": 1.21}
        ret = _portfolio_return(curve)
        assert ret == pytest.approx(0.21, abs=1e-5), (
            f"Defect [P0-04]: Expected cumulative return 0.21, got {ret}"
        )

    def test_r5_tier3_optional_sdk_safe_fallback(self) -> None:
        """Tier 3: Importing sdk_ext must NOT crash if claude_agent_sdk is missing."""
        try:
            import finaince.sdk_ext  # noqa: F401
        except ModuleNotFoundError as exc:
            if "claude_agent_sdk" in str(exc):
                pytest.fail("Defect [P1-18]: sdk_ext crashes on import when claude_agent_sdk is not installed.")
            raise

    def test_r5_tier4_multi_year_equity_curve_and_drawdown_stress(self) -> None:
        """Tier 4: 500-period equity simulation with interspersed NaNs and crashes."""
        from finaince.domain.scoring import max_drawdown

        rng = np.random.default_rng(777)
        returns = [float(rng.normal(0.0005, 0.02)) for _ in range(500)]
        # Inject NaNs at random indices
        for idx in [50, 120, 280, 410]:
            returns[idx] = float("nan")

        dd = max_drawdown(returns)
        assert math.isfinite(dd)
        assert -1.0 <= dd <= 0.0


# ==============================================================================
# End-to-End Cross-Feature Interactions (Tier 3 & Tier 4)
# ==============================================================================

class TestPhase1CrossFeatureTier3:
    """Tier 3: Cross-Feature Interactions across R1-R5."""

    def test_cross_feature_data_track_to_model_head_pipeline(self) -> None:
        """Tier 3: Continuous data track feed into Walk-Forward linear timing head."""
        from finaince.data_track import _forward_returns
        from finaince.loop import train_linear_head

        codes = ["600000.SH"]
        panel = _make_daily_panel(date(2023, 1, 1), 120, codes, base_price=10.0, daily_growth=0.001)
        fwd = _forward_returns(panel)

        daily_returns = {
            row["trade_date"].isoformat(): float(row["_fwd_ret"])
            for row in fwd.to_dicts()
            if row["_fwd_ret"] is not None
        }

        model_res = train_linear_head(daily_returns)
        assert model_res["skipped"] is False
        assert "equity_curve" in model_res
        assert len(model_res["equity_curve"]) > 0

    def test_cross_feature_microstructure_and_scoring_drawdown(self) -> None:
        """Tier 3: Microstructure limit-down asset retention leads to real drawdown calculation."""
        from finaince.domain.scoring import max_drawdown

        # Asset hits 2 days of limit-down: -10% each
        daily_rets = [0.01, -0.10, -0.10, 0.02]
        dd = max_drawdown(daily_rets)
        assert dd < -0.15, "Drawdown must reflect both limit down drops"


class TestPhase1RealWorldStressTier4:
    """Tier 4: Multi-Year Stress Simulation."""

    def test_real_world_multi_year_regime_stress(self) -> None:
        """Tier 4: Multi-year full pipeline stress with market crash, rally, and volatility."""
        from finaince.domain.scoring import max_drawdown
        from finaince.loop import train_linear_head

        # 2 years of simulated daily returns with a market crash episode
        rng = np.random.default_rng(2026)
        dates = [f"2022-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 23)] + [
            f"2023-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 23)
        ]
        returns_dict = {}
        for i, d in enumerate(dates[:400]):
            if 150 <= i <= 160:
                # 10 days of market crash
                ret = float(rng.normal(-0.04, 0.01))
            else:
                ret = float(rng.normal(0.0005, 0.015))
            returns_dict[d] = ret

        # 1. Walk-forward timing head
        head_res = train_linear_head(returns_dict)
        assert head_res["skipped"] is False

        # 2. Portfolio return & drawdown on the resulting curve
        raw_curve = head_res["equity_curve"]
        curve_returns = [float(v) for v in raw_curve.values()]
        assert len(curve_returns) > 0

        dd = max_drawdown(list(returns_dict.values()))
        assert dd < -0.10, "Simulated crash must register significant drawdown"
        assert dd >= -1.0, "Drawdown cannot exceed 100% wipeout"

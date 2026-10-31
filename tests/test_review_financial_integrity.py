"""Independent mathematical and fail-closed regressions for the review fixes."""

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from finaince.combination import _rolling_inverse_vol_weights, combine_daily
from finaince.data_track import _daily_ic, _factor_scores, _layered_long_short, _scoped_universe
from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
from finaince.eval.pit_core import score_expression, score_frame_window
from finaince.eval.router import EvalRequest, _evaluate, _validate_lag_windows
from finaince.review.gates import evaluate_gates, ic_t_stat


def _record(*, icir=1.0, returns=None, n_names=30):
    now = datetime.now(UTC)
    return FactorRecord(
        id="fac_integrity",
        name="integrity",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        engine="pit_core",
        window_start="2024-01-01",
        window_end="2024-04-01",
        citable=True,
        metrics=FactorMetrics(ic=0.1, icir=icir, n_names=n_names),
        daily_returns=returns if returns is not None else {
            (date(2024, 1, 1) + timedelta(days=i)).isoformat(): 0.001 + (i % 2) * 0.002
            for i in range(40)
        },
        lineage=FactorLineage(source="manual", source_ref="integrity"),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), float("-inf")])
def test_missing_or_nonfinite_icir_blocks_promotion(value, isolated_home):
    result = evaluate_gates(_record(icir=value), direction="to_pool", override=["homogeneous"])
    assert not result["passed"]
    assert "weak_ic" in result["failures"]
    assert ic_t_stat(value, 40) is None


@pytest.mark.parametrize("returns", [
    {f"d{i}": 0.001 + (i % 2) * 0.002 for i in range(19)},
    {f"d{i}": 0.001 for i in range(40)},
])
def test_unverifiable_dsr_blocks_promotion(returns, isolated_home):
    result = evaluate_gates(_record(returns=returns), direction="to_pool", override=["homogeneous"])
    assert not result["passed"]
    assert "inflated_sharpe" in result["failures"]


def test_sample_gate_uses_evaluated_record_not_active_local_panel(isolated_home, monkeypatch):
    monkeypatch.setattr("finaince.runtime.local_panel_stats", lambda: {"n_assets": 2, "thin": True})
    broad = evaluate_gates(_record(), direction="to_pool", override=["homogeneous"])
    assert "thin_panel" not in broad["failures"]
    monkeypatch.setattr("finaince.runtime.local_panel_stats", lambda: {"n_assets": 300, "thin": False})
    monkeypatch.setattr("finaince.runtime.local_panel_is_thin", lambda: False)
    thin = evaluate_gates(_record(n_names=10), direction="to_pool", override=["homogeneous"])
    assert "thin_panel" in thin["failures"]


def _cross_section(sign=1.0):
    day = date(2024, 1, 2)
    codes = [f"S{i:02}" for i in range(20)]
    scores = pl.DataFrame({"trade_date": [day] * 20, "ts_code": codes, "_score": list(range(20))})
    forwards = pl.DataFrame({
        "trade_date": [day] * 20,
        "ts_code": codes,
        "_fwd_ret": [sign * i / 1000 for i in range(20)],
    })
    return scores, forwards, {day: set(codes)}


@pytest.mark.parametrize("spearman", [False, True])
@pytest.mark.parametrize("sign", [-1.0, 1.0])
def test_perfect_correlation_is_exactly_one_in_magnitude(spearman, sign):
    scores, forwards, universe = _cross_section(sign)
    assert _daily_ic(scores, forwards, universe, spearman=spearman) == pytest.approx([sign])


def test_positive_factor_buys_high_group_and_sells_low_group():
    scores, forwards, universe = _cross_section()
    series, _ = _layered_long_short(scores, forwards, universe)
    # Mean(16,17,18,19) - Mean(0,1,2,3), in basis-point returns.
    assert series[0][1] == pytest.approx(0.016)
    negative, _ = _layered_long_short(scores.with_columns(-pl.col("_score")), forwards, universe)
    assert negative[0][1] == pytest.approx(-0.016)


def test_sweep_cadence_uses_same_long_short_direction():
    from finaince.sweep import _layered_long_short_cadence

    scores, forwards, universe = _cross_section()
    base, _ = _layered_long_short(scores, forwards, universe)
    cadence, _ = _layered_long_short_cadence(scores, forwards, universe, rebalance_every=5)
    assert cadence == base


def test_infinite_values_do_not_become_valid_rank_correlations():
    scores, forwards, universe = _cross_section()
    forwards = forwards.with_columns(
        pl.when(pl.col("ts_code") == "S19").then(float("inf")).otherwise(pl.col("_fwd_ret"))
        .alias("_fwd_ret")
    )
    assert _daily_ic(scores, forwards, universe, spearman=True) == pytest.approx([1.0])
    assert _layered_long_short(scores, forwards, universe)[0] == []


def _prices(n_assets=20, n_days=35):
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
    frame = pl.DataFrame([
        {"trade_date": day, "ts_code": f"S{i:02}", "close": 10 * (1 + i / 1000) ** j}
        for j, day in enumerate(days) for i in range(n_assets)
    ])
    return frame, days


def test_rank_preserves_ties_and_excludes_nonfinite_factor_values():
    frame, days = _prices()
    constant = frame.with_columns(pl.lit(10.0).alias("close"))
    scores = score_expression(constant, "Rank(Delta(close, 1))")
    assert scores.filter(pl.col("trade_date") == days[-1])["_score"].n_unique() == 1
    seed_scores = _factor_scores(constant, "rank_delta_20")
    assert seed_scores.filter(pl.col("trade_date") == days[-1])["_score"].n_unique() == 1
    invalid = frame.with_columns(
        pl.when(pl.col("ts_code") == "S00").then(float("nan"))
        .when(pl.col("ts_code") == "S01").then(float("inf"))
        .otherwise(pl.col("close")).alias("close")
    )
    scores = score_expression(invalid, "Rank(Delta(close, 1))")
    excluded = scores.filter(pl.col("ts_code").is_in(["S00", "S01"]))
    assert excluded["_score"].null_count() == excluded.height

    from polars.testing import assert_frame_equal

    from finaince.sweep import _score_spec, score_candidate

    spec = {"template": "mom", "field": "close", "window": 1}
    assert_frame_equal(scores, score_candidate(invalid, spec))
    assert_frame_equal(scores, _score_spec(invalid.sort(["ts_code", "trade_date"]), spec))


def test_citable_requires_real_cross_section_not_universe_claim():
    frame, days = _prices(n_assets=10)
    claimed = {day: {f"S{i:02}" for i in range(300)} for day in days}
    metrics = score_frame_window(frame, "Rank(Delta(close, 1))", claimed, days[0], days[-1], 0.0)
    assert metrics["n_names"] == 10
    assert metrics["min_names"] == 10
    assert metrics["citable"] is False
    assert metrics["daily_returns"] == {}


def test_single_day_window_embargo_excludes_future_return():
    day = date(2024, 1, 2)
    assert _scoped_universe({day: {"S00"}}, day, day, embargo=True) == {}


@pytest.mark.parametrize("expression", [
    "Ref(close, -1)", "Delta(close, 1 - 2)", "Delay(close, -(2 / 2))",
    "Ref(close, n=-1)", "Ref(close, **{'n': -1})", "close.shift(periods=-1)",
    "Ref(close, volume)", "Ref(close, 0.5)", "Ref(close, 1e309)",
])
@pytest.mark.parametrize("dialect", ["repro_polars", "qlib"])
def test_router_rejects_future_or_unverifiable_lags_before_engine_dispatch(expression, dialect):
    if dialect == "qlib":
        expression = expression.replace("close", "$close").replace("volume", "$volume")
    result = _evaluate(EvalRequest(expression=expression, dialect=dialect))
    assert not result.ok
    assert result.error.startswith(("future_data:", "invalid_lag_window:"))


@pytest.mark.parametrize("expression", ["Ref(close, 1)", "Delta(close, 2 * 10)", "Ref(close, 0)"])
def test_router_keeps_valid_causal_lags(expression):
    assert _validate_lag_windows(expression, "repro_polars") is None


def test_panel_cache_uses_each_loader_path_and_convertible_file(monkeypatch, tmp_path):
    from reproagent.reproducer.data_loader import DataLoader
    from reproagent.settings import Settings

    from finaince.eval import router

    router._PANEL_CACHE.clear()
    monkeypatch.setenv("FINAINCE_PANEL_CACHE", "on")
    router._install_panel_cache()
    loaders = []
    for folder, value in [("a", 10.0), ("b", 20.0)]:
        root = tmp_path / folder
        root.mkdir()
        frame = pl.DataFrame({
            "trade_date": [date(2024, 1, 2)], "ts_code": ["S00"],
            "open": [value], "high": [value], "low": [value], "close": [value], "volume": [100.0],
        })
        frame.write_parquet(root / "prices.parquet")
        frame.write_parquet(root / "cb_prices.parquet")
        loaders.append(DataLoader(Settings(_env_file=None, data_source="local", local_data_path=root)))
    start = end = date(2024, 1, 2)
    assert loaders[0].load_price_data("local_panel", start, end)["close"].item() == 10.0
    assert loaders[1].load_price_data("local_panel", start, end)["close"].item() == 20.0
    assert loaders[0].load_price_data("cb", start, end)["close"].item() == 10.0
    changed = frame.with_columns(pl.lit(30.0).alias("close"))
    changed.write_parquet(tmp_path / "a" / "cb_prices.parquet")
    assert loaders[0].load_price_data("cb", start, end)["close"].item() == 30.0


def _member_returns():
    return {
        fid: {
            (date(2024, 1, 1) + timedelta(days=i)).isoformat(): 0.001 + ((i + offset) % 3) / 1000
            for i in range(40)
        }
        for fid, offset in [("a", 0), ("b", 1)]
    }


def test_current_missing_return_does_not_change_current_weights():
    rows = {fid: list(series.values()) for fid, series in _member_returns().items()}
    before = _rolling_inverse_vol_weights(rows, lookback=20)
    rows["a"][25] = None
    after = _rolling_inverse_vol_weights(rows, lookback=20)
    assert before[:26] == after[:26]


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf")])
def test_combination_cannot_erase_missing_held_factor_returns(missing):
    members = _member_returns()
    day = sorted(members["a"])[25]
    if missing is None:
        del members["a"][day]
    else:
        members["a"][day] = missing
    result = combine_daily(members)
    assert result == {"ok": False, "reason": "missing_member_returns", "date": day, "members": ["a"]}


def test_combination_charges_initial_position_and_requires_member_count():
    members = _member_returns()
    free = combine_daily(members, cost_bps=0.0)
    paid = combine_daily(members, cost_bps=10.0)
    first = min(free["daily_returns"])
    assert free["daily_returns"][first] - paid["daily_returns"][first] == pytest.approx(0.001)
    assert combine_daily({"a": members["a"]})["reason"] == "insufficient_members"


def test_split_day_flat_bar_is_not_misclassified_as_limit_move():
    from finaince.data_track import _forward_returns

    df = pl.DataFrame({
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "open": [100.0, 50.0, 55.0],
        "high": [101.0, 50.0, 55.0],
        "low": [99.0, 50.0, 55.0],
        "close": [100.0, 50.0, 55.0],
        "volume": [1000.0, 1000.0, 1000.0],
        "adj_factor": [1.0, 2.0, 2.0],
    })
    fwd = _forward_returns(df).sort("trade_date")["_fwd_ret"].to_list()
    # Day 1 -> Day 2 is a 2-for-1 split with 0% true return (not a -50% limit down)
    assert fwd[0] == pytest.approx(0.0)
    # Day 2 -> Day 3 is a genuine +10% one-word limit-up bar, which must be withheld
    assert fwd[1] is None


def test_neutralization_date_cross_section_and_null_alignment():
    from reproagent.reproducer import backtester as bt

    from finaince._reproagent_compat import ensure_reproagent_compat, neutralize_cross_sectional

    ensure_reproagent_compat()
    df = pl.DataFrame({
        "date": ["2024-01-02"] * 4 + ["2024-01-03"] * 4,
        "industry": ["bank", "bank", "tech", "tech", "bank", "bank", "tech", "tech"],
        "factor_value": [10.0, 12.0, None, 20.0, 100.0, 102.0, 200.0, 204.0],
        "log_mcap": [1.0, 2.0, 3.0, 4.0, 1.0, 2.0, 3.0, 4.0],
    })
    ind_series = bt.neutralize_industry(df["factor_value"], df["industry"], date=df["date"])
    assert len(ind_series) == len(df)
    assert ind_series[2] is None
    # On 2024-01-02 bank mean is 11.0 (not polluted by 2024-01-03 bank mean 101.0)
    assert ind_series[0] == pytest.approx(-1.0)
    assert ind_series[1] == pytest.approx(1.0)
    assert ind_series[4] == pytest.approx(-1.0)
    assert ind_series[5] == pytest.approx(1.0)

    out_df = neutralize_cross_sectional(df)
    assert out_df.height == df.height
    assert out_df["neutral_factor"][2] is None


def test_sharpe_and_equity_curve_nan_resilience():
    import math

    from finaince.domain.scoring import equity_curve, sharpe_ratio

    vals = [0.01 + (i % 3) * 0.005 for i in range(25)] + [float("nan"), float("inf")]
    sr = sharpe_ratio(vals)
    assert sr is not None and math.isfinite(sr)

    curve = equity_curve({"2024-01-01": 0.1, "2024-01-02": float("nan"), "2024-01-03": -0.1})
    assert list(curve.keys()) == ["2024-01-01", "2024-01-03"]
    assert curve["2024-01-03"] == pytest.approx(0.99)

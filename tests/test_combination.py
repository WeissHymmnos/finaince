"""WS-I dynamic combination + net-reward bandit tests."""

from __future__ import annotations

import math


def _dates(n: int) -> list[str]:
    from datetime import date, timedelta

    base = date(2024, 1, 1)
    return [(base + timedelta(days=i)).isoformat() for i in range(n)]


def _members(n_days: int = 120):
    dates = _dates(n_days)
    rng_a = [0.001 * math.sin(i / 7.0) + 0.0004 * ((-1) ** (i % 2)) for i in range(n_days)]
    rng_b = [0.001 * math.cos(i / 9.0) + 0.0004 * ((-1) ** ((i + 1) % 2)) for i in range(n_days)]
    return (
        {"fac_a": dict(zip(dates, rng_a)), "fac_b": dict(zip(dates, rng_b))},
        dates,
    )


def test_combine_daily_produces_net_metrics() -> None:
    from finaince.combination import combine_daily

    members, _ = _members()
    out = combine_daily(members, lookback=20, cost_bps=5.0)
    assert out["ok"] is True
    assert set(out["members"]) == {"fac_a", "fac_b"}
    assert out["n_days"] >= 100
    assert 0.0 <= out["mean_turnover"] <= 1.0
    assert isinstance(out["sharpe_net"], float)
    assert len(out["equity_curve"]) == out["n_days"]


def test_weights_use_only_past_information() -> None:
    """Perturbing day t+50 returns must not change weights on days <= t+49."""
    import polars.testing as _pt  # noqa: F401  # ensure plugin load parity

    from finaince.combination import _rolling_inverse_vol_weights

    members, _ = _members(120)
    rows = {
        fid: [series[day] for day in sorted(series)]
        for fid, series in members.items()
    }
    baseline = _rolling_inverse_vol_weights(rows, lookback=20)

    perturbed = {fid: list(vals) for fid, vals in rows.items()}
    perturbed["fac_a"][80] += 10.0
    after = _rolling_inverse_vol_weights(perturbed, lookback=20)

    for index in range(80):
        assert baseline[index] == after[index]


def test_combo_beats_best_member_on_diversified_pair() -> None:
    from finaince.combination import combo_vs_best_member

    n = 220
    dates = _dates(n)
    exposure = [math.sin(i / 3.0) + 0.5 * math.sin(i / 7.0) for i in range(n)]
    long_leg = [0.0004 + 0.002 * e for e in exposure]
    hedge_leg = [0.0003 - 0.002 * e + 0.0002 * math.sin(i / 11.0) for i, e in enumerate(exposure)]
    payload = {"long_leg": dict(zip(dates, long_leg)), "hedge_leg": dict(zip(dates, hedge_leg))}
    verdict = combo_vs_best_member(payload, lookback=40, cost_bps=1.0)
    assert verdict["ok"] is True
    assert verdict["combo_sharpe_net"] is not None
    assert verdict["best_member_sharpe"] is not None
    assert verdict["beats_best"] is True


def test_try_combine_ready_skips_without_ready_factors(isolated_home) -> None:
    from finaince.combination import try_combine_ready

    out = try_combine_ready(min_factors=2)
    assert out["ok"] is False
    assert out["reason"] == "insufficient_ready_factors"


def test_choose_next_action_uses_net_reward(monkeypatch) -> None:
    from finaince.loop import choose_next_action

    positive_port_negative_sharpe = [
        {
            "action": "model",
            "ok": True,
            "metrics": {"portfolio_return": 0.05, "sharpe_net": -0.3},
        }
    ]
    assert choose_next_action(positive_port_negative_sharpe) == "factor"

    negative_port_positive_sharpe = [
        {
            "action": "model",
            "ok": True,
            "metrics": {"portfolio_return": -0.01, "sharpe_net": 1.2},
        }
    ]
    assert choose_next_action(negative_port_positive_sharpe) == "model"

    legacy_history = [{"action": "model", "ok": True, "metrics": {"portfolio_return": 0.02}}]
    assert choose_next_action(legacy_history) == "model"


def test_run_model_step_reports_combination_payload(monkeypatch, isolated_home) -> None:
    from finaince.loop import run_model_step

    step = run_model_step({"daily_returns": {f"2024-01-{d:02d}": d / 500.0 for d in range(1, 31)}})
    assert step["action"] == "model"
    assert step["skipped"] is False
    assert "combination" in step
    combo = step["combination"]
    assert combo.get("skipped") is True or combo.get("sharpe_net") is not None

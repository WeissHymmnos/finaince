"""Drive shipped aiminer discovery: selection_score + evaluate_and_combine cull."""

from __future__ import annotations

from finaince.discovery import cull_factor_pool, score_factor


def _returns(offset: float) -> dict[str, float]:
    return {f"2024-01-{day:02d}": offset + day / 100.0 for day in range(1, 13)}


def test_score_factor_uses_shipped_selection_score() -> None:
    from aiminer.core.strategy import selection_score

    strong = {
        "annualized_return": 0.22,
        "sharpe": 1.8,
        "max_drawdown": 0.08,
        "turnover": 0.2,
        "cost_drag": 0.01,
    }
    weak = {
        "annualized_return": 0.01,
        "sharpe": 0.1,
        "max_drawdown": 0.4,
        "turnover": 1.5,
        "cost_drag": 0.05,
    }
    strong_score = score_factor(strong, factor_ic=0.06)
    weak_score = score_factor(weak, factor_ic=0.0)
    assert strong_score == selection_score(strong, factor_ic=0.06)
    assert weak_score == selection_score(weak, factor_ic=0.0)
    assert isinstance(strong_score, float)
    assert strong_score > weak_score


def test_cull_factor_pool_keep_and_cull_via_shipped_selector() -> None:
    from aiminer.manager import cull_alpha_pool

    shared = _returns(0.0)
    independent = {f"2024-02-{day:02d}": 0.02 * ((-1) ** day) for day in range(1, 13)}
    candidates = [
        {
            "role": "momentum",
            "hypothesis": "keep-me",
            "perf_metric": 0.04,
            "selection_score": 0.04,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "copycat",
            "hypothesis": "correlated-cull",
            "perf_metric": 0.03,
            "selection_score": 0.03,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "noise",
            "hypothesis": "below-threshold",
            "perf_metric": 0.001,
            "selection_score": 0.001,
            "market_profile": "cn_stock",
            "returns": independent,
        },
        {
            "role": "sim",
            "hypothesis": "simulated-cull",
            "perf_metric": 0.2,
            "is_simulated": True,
            "returns": independent,
        },
    ]

    kept = cull_factor_pool(candidates)
    assert kept == cull_alpha_pool(candidates)
    names = {item["hypothesis"] for item in kept}
    assert "keep-me" in names
    assert "correlated-cull" not in names
    assert "below-threshold" not in names
    assert "simulated-cull" not in names
    assert all(str(item.get("id", "")).startswith("alpha_") for item in kept)
    assert all("perf_metric" in item for item in kept)

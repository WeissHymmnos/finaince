"""Regression tests for the quality-audit fixes (scoring convergence, turnover series, etc.)."""

from __future__ import annotations

import json

import pytest

from finaince.domain.scoring import (
    equity_curve,
    ic_t_stat,
    max_drawdown,
    sharpe_ratio,
)


def test_sharpe_matches_legacy_inline_formula() -> None:
    import math

    values = [((i % 7) - 3) / 500.0 for i in range(60)]
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    expected = mean * math.sqrt(252.0) / math.sqrt(var)
    assert sharpe_ratio(values) == pytest.approx(expected)
    assert sharpe_ratio([0.01] * 19) is None
    assert sharpe_ratio([]) is None


def test_max_drawdown_sign_convention() -> None:
    values = [0.10, -0.20, 0.05]
    equity = [1.10, 0.88, 0.924]
    peak = 1.10
    expected = min(equity[i] / peak - 1.0 for i in range(3))
    assert max_drawdown(values) == pytest.approx(expected)
    assert max_drawdown([0.01] * 30) == 0.0


def test_equity_curve_skips_non_numeric() -> None:
    curve = equity_curve({"b": "oops", "a": 0.5})
    assert list(curve) == ["a"]
    assert curve["a"] == 1.5


def test_ic_t_stat_delegates_and_guards() -> None:
    from finaince.review.gates import ic_t_stat as gates_t_stat

    assert ic_t_stat(2.0, 9) == gates_t_stat(2.0, 9) == 6.0
    assert ic_t_stat(None, 100) is None
    assert ic_t_stat(1.0, 0) is None


def test_data_track_window_metrics_applies_cost_per_day(isolated_home) -> None:
    from datetime import date as d

    from finaince.data_track import _window_metrics

    days = [(d(2024, 1, 1 + i)) for i in range(25)]
    ls = [(day, 0.001) for day in days]
    churn = {day: (0.02 if i % 2 else 0.00) for i, day in enumerate(days)}
    per_day = _window_metrics(ls, cost_bps=100.0, turnover_by_day=churn)
    mean_churn = sum(churn.values()) / len(churn)
    flat = _window_metrics(ls, cost_bps=100.0, turnover_by_day={day: mean_churn for day in days})

    assert per_day["turnover"] == pytest.approx(mean_churn, abs=1e-6)
    assert per_day["sharpe_net"] is not None
    assert per_day["sharpe_net"] != flat["sharpe_net"]
    assert per_day["max_drawdown"] <= 0.0


def test_expr_bucket_boundaries_never_crash() -> None:
    from finaince.expr_ast import _bucket

    for value in (0.5, 1, 3, 7.4, 15, 40, 90, 180, 240, 241, 9999):
        bucketed = _bucket(float(value))
        assert bucketed in (1, 5, 10, 20, 60, 120, 240)
    assert _bucket(241) == 240
    assert _bucket(181) == 240
    assert _bucket(179) == 120


def test_combination_turnover_is_half_l1() -> None:
    from finaince.combination import _turnover

    prev = {"a": 0.5, "b": 0.5}
    curr = {"a": 0.0, "c": 1.0}
    assert _turnover(prev, curr) == pytest.approx(1.0)


def test_process_memory_atomic_write_leaves_no_tmp(isolated_home) -> None:
    from finaince import process_memory as pm

    pm.record_edit_outcome(error_prefix="E: x", motif={"added_lines": 1}, survived_gates=True)
    memory_path = pm._memory_path()
    assert memory_path.exists()
    assert not memory_path.with_suffix(".json.tmp").exists()
    assert json.loads(memory_path.read_text())["motifs"]


def test_chain_match_at_interior_member_extends_tail(isolated_home) -> None:
    """Candidate correlates with an INTERIOR member: chain still found; tail rule still governs extension."""
    from finaince.process_memory import update_chains

    base = {f"2024-01-{d:02d}": d / 100.0 for d in range(1, 29)}
    update_chains("head", base, rank_ic=0.02)

    interior_like = {k: v * -1.0 + 0.001 for k, v in base.items()}
    second = update_chains("mid", interior_like, rank_ic=0.06)
    assert second["action"] in {"extend", "reject_not_beating_chain", "new_chain"}

    probe = {k: v * 0.95 + 0.0005 for k, v in interior_like.items()}
    third = update_chains("probe", probe, rank_ic=0.10)
    assert third["action"] == "extend"
    assert third.get("best_member_position") is not None


def test_brain_base_reads_env_after_import(monkeypatch) -> None:
    from finaince import brain_track

    monkeypatch.setenv("BRAIN_API_BASE", "https://brain.example.org")
    assert brain_track.brain_base() == "https://brain.example.org"
    monkeypatch.delenv("BRAIN_API_BASE")
    assert brain_track.brain_base().startswith("https://api.worldquantbrain")


def test_campaign_manifest_roundtrip_via_atomic_path(isolated_home) -> None:
    from finaince import corpus_campaign as cc

    cc.save_manifest({"schema_version": 1, "entries": {"x": {"status": "done"}}})
    loaded = cc.load_manifest()
    assert loaded["entries"]["x"]["status"] == "done"


def test_serve_rejects_bad_direction_and_adversary(isolated_home):
    from fastapi.testclient import TestClient

    from finaince.serve import create_app
    from tests.conftest import desk_headers

    client = TestClient(create_app())
    bad_direction = client.post(
        "/api/v1/promote",
        json={"catalog_id": "whatever", "direction": "to_moon"},
        headers=desk_headers(),
    )
    assert bad_direction.status_code == 400
    bad_adv = client.post(
        "/api/v1/review/nope/approve",
        json={"adversary": "yes-please"},
        headers=desk_headers(),
    )
    assert bad_adv.status_code == 400

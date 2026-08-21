"""WS-L process memory + experience-chain tests."""

from __future__ import annotations

import pytest

from finaince import process_memory as pm


@pytest.fixture(autouse=True)
def _clean(isolated_home):
    yield


MOTIF = {"added_lines": 3, "removed_lines": 1, "changed_regions": 1, "signatures_equal": False}


def test_record_and_recall_weighted_by_gate_survival() -> None:
    for _ in range(3):
        out = pm.record_edit_outcome(error_prefix="ValueError: shape", motif=MOTIF, survived_gates=True)
        assert out["ok"] is True
    pm.record_edit_outcome(error_prefix="ValueError: shape", motif=MOTIF, survived_gates=False)

    motifs = pm.recall_motifs(error_prefix="ValueError: shape")
    assert len(motifs) == 1
    entry = motifs[0]
    assert entry["attempts"] == 4
    assert entry["survived"] == 3
    assert entry["weight"] == 5.0
    assert "ValueError" in pm.context_block(error_prefix="ValueError: shape")


def test_recall_filters_by_error_class() -> None:
    pm.record_edit_outcome(error_prefix="TypeError: bad", motif=dict(MOTIF), survived_gates=True)
    pm.record_edit_outcome(error_prefix="KeyError: missing", motif=dict(MOTIF), survived_gates=False)
    only_type = pm.recall_motifs(error_prefix="TypeError: x")
    assert len(only_type) == 1
    assert only_type[0]["error_prefix"].startswith("TypeError")
    context = pm.context_block(error_prefix="KeyError: y")
    assert "KeyError" in context and "TypeError" not in context


def test_context_block_empty_when_no_memory() -> None:
    assert pm.context_block() == ""


def test_chain_new_extend_reject_rules() -> None:
    base_returns = {f"2024-01-{d:02d}": d / 100.0 for d in range(1, 25)}
    first = pm.update_chains("rec_1", base_returns, rank_ic=0.03)
    assert first["action"] == "new_chain"

    correlated_tail = {k: v * 0.9 + 0.001 for k, v in base_returns.items()}
    extend = pm.update_chains("rec_2", correlated_tail, rank_ic=0.05)
    assert extend["action"] == "extend"
    assert extend["chain"] == 0

    weak_beater = {k: v * 0.95 + 0.0005 for k, v in base_returns.items()}
    reject = pm.update_chains("rec_3", weak_beater, rank_ic=0.04)
    assert reject["action"] == "reject_not_beating_chain"

    uncorrelated = {
        f"2024-01-{d:02d}": ((-1) ** d) * 0.01 + (d % 7) / 1000.0 for d in range(1, 25)
    }
    fresh = pm.update_chains("rec_4", uncorrelated, rank_ic=0.02)
    assert fresh["action"] == "new_chain"
    assert fresh["n_chains"] == 2

    display = pm.chains_display()
    assert display[0]["length"] == 2
    assert display[0]["best_rank_ic"] == 0.05


def test_memory_survives_round_trip(isolated_home) -> None:
    pm.record_edit_outcome(error_prefix="E1: a", motif=dict(MOTIF), survived_gates=True)
    reloaded = pm.load_memory()
    signature = next(iter(reloaded["motifs"]))
    assert reloaded["motifs"][signature]["attempts"] == 1
    assert pm.load_chains()["schema_version"] == 1


def test_research_context_exposes_chains(isolated_home) -> None:
    from finaince.coaching import research_context

    pm.update_chains("rec_x", {f"2024-01-{d:02d}": d / 100.0 for d in range(1, 20)}, 0.04)
    ctx = research_context(sample_limit=2, lesson_limit=2)
    assert ctx["ok"] is True
    assert isinstance(ctx.get("chains"), list)


def test_advise_action_prompt_includes_memory(monkeypatch, isolated_home) -> None:
    import finaince.loop as loop_mod

    pm.record_edit_outcome(error_prefix="NameError: x", motif=dict(MOTIF), survived_gates=True)
    seen_prompts = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"action":"model","hypothesis":"h"}'}}]}

    def fake_post(url, **kwargs):
        seen_prompts.append(kwargs["json"]["messages"][0]["content"])
        return FakeResponse()

    monkeypatch.setenv("FINAINCE_LOOP_ADVISOR", "1")
    import httpx as httpx_mod

    import finaince.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "resolve_llm", lambda *a, **k: {"base_url": "http://x", "model": "m", "api_key": "k"})
    monkeypatch.setattr(httpx_mod, "post", fake_post)
    advice = loop_mod.advise_action([])
    assert advice["via"] == "llm"
    assert any("process memory" in p for p in seen_prompts)

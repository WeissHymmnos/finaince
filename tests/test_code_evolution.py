"""WS-H code-factor evolution tests (hermetic, no chat provider)."""

from __future__ import annotations

from finaince.code_evolution import (
    ast_edit_motive,
    build_rewrite_prompt,
    default_seed,
    evolve_code_factor,
)

GOOD_SOURCE = """\
NAME = 'evo_ok'
EXPRESSION = ''
def compute(panel):
    close = list(panel['close'])
    return [b - a for a, b in zip(close, close[1:])]
"""

BAD_SOURCE = """\
NAME = 'evo_bad'
EXPRESSION = ''
def compute(panel):
    close = panel['close']
    return [open('x') for _ in close]
"""


def test_default_seed_passes_sandbox_and_governance(isolated_home) -> None:
    result = evolve_code_factor(
        "rolling mean drift factor",
        seed_source=default_seed("evo_seed"),
        rounds=1,
    )
    assert result["ok"] is True, result
    assert result["stage"] == "governed"
    assert result["catalog_id"]
    assert result["final_via"] in {"frozen_builtin", "bwrap"}
    gates = result["gates"]
    assert set(gates) >= {"passed", "failures", "details"}


def test_failing_draft_without_llm_stops_honestly(monkeypatch, isolated_home) -> None:
    monkeypatch.setenv("ALLOW_MOCK_LLM", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = evolve_code_factor(
        "broken on purpose",
        seed_source=BAD_SOURCE,
        rounds=2,
    )
    assert result["ok"] is False
    assert result["stage"] in {"llm_unavailable", "no_rewrite"}
    assert len(result["drafts"]) == 1
    assert result["drafts"][0]["ok"] is False
    assert "open" in str(result["error"]).lower() or result["error"]


def test_rewrite_prompt_contains_failure_context() -> None:
    prompt = build_rewrite_prompt(
        "momentum hypothesis",
        previous_source=BAD_SOURCE,
        error="NameError: open is not allowed",
        lessons=[{"error": "PermissionError: open is not allowed in isolator", "summary": "past"}],
    )
    assert "momentum hypothesis" in prompt
    assert "compute(panel)" in prompt
    assert "NameError" in prompt
    assert "Similar past failures" in prompt


def test_ast_edit_motive_counts_changes() -> None:
    motive = ast_edit_motive(BAD_SOURCE, GOOD_SOURCE)
    assert motive["added_lines"] >= 0
    assert motive["removed_lines"] >= 0
    assert motive["changed_regions"] >= 1
    identical = ast_edit_motive(GOOD_SOURCE, GOOD_SOURCE)
    assert identical == {
        "added_lines": 0,
        "removed_lines": 0,
        "changed_regions": 0,
        "signatures_equal": True,
    }
    signature_only = ast_edit_motive(
        "def compute(a):\n    return 1\n",
        "def compute(b):\n    return 1\n",
    )
    assert signature_only["signatures_equal"] is False


def test_llm_unavailable_stage_reports_prompt_preview(monkeypatch, isolated_home) -> None:
    monkeypatch.setenv("ALLOW_MOCK_LLM", "false")
    result = evolve_code_factor(
        "needs a rewrite",
        seed_source=BAD_SOURCE,
        rounds=3,
    )
    assert result["ok"] is False
    assert result["stage"] in {"llm_unavailable", "no_rewrite"}
    assert result["rounds_attempted"] == 1
    assert "compute(panel)" in result["prompt_preview"]

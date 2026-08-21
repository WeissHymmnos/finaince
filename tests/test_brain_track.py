"""WS-J BRAIN adjudication tests (hermetic — no network, no credentials)."""

from __future__ import annotations

from finaince.brain_track import (
    _extract_alpha_id,
    adjudicate,
    build_simulation_payload,
    has_brain_credentials,
)


def test_credentials_gate(monkeypatch) -> None:
    monkeypatch.delenv("BRAIN_USER", raising=False)
    monkeypatch.delenv("BRAIN_PASS", raising=False)
    assert has_brain_credentials() is False
    monkeypatch.setenv("BRAIN_USER", "u")
    monkeypatch.setenv("BRAIN_PASS", "p")
    assert has_brain_credentials() is True


def test_simulation_payload_defaults_and_overrides() -> None:
    payload = build_simulation_payload("rank(close)")
    assert payload["type"] == "REGULAR"
    assert payload["regular"] == "rank(close)"
    assert payload["settings"]["region"] == "CHN"
    assert payload["settings"]["language"] == "FASTEXPR"

    custom = build_simulation_payload("rank(open)", settings={"region": "USA"})
    assert custom["settings"]["region"] == "USA"
    assert custom["settings"]["neutralization"] == "SUBINDUSTRY"


def test_extract_alpha_id() -> None:
    assert _extract_alpha_id("https://api.worldquantbrain.com/alphas/abc123") == "abc123"
    assert _extract_alpha_id("/simulations/xyz/") == "xyz"
    assert _extract_alpha_id("") is None


def test_adjudicate_degrades_without_credentials(monkeypatch, isolated_home) -> None:
    monkeypatch.delenv("BRAIN_USER", raising=False)
    monkeypatch.delenv("BRAIN_PASS", raising=False)
    result = adjudicate("Rank(Delta(close, 5))")
    assert result["ok"] is False
    assert result["adjudication_level"] == "none"
    assert result["reason"] == "no_credentials"
    assert result["degraded_to"]["tool"].startswith("finaince bench")


def test_write_back_ignores_failed_rulings(isolated_home) -> None:
    from finaince.brain_track import write_back

    assert write_back("whatever", {"ok": False}) is None
    assert write_back("", {"ok": True, "alpha_id": "x"}) is None

"""FINAINCE_NO_PATH_HACK kill switch and extras declaration."""

from __future__ import annotations

from pathlib import Path

from finaince._paths import ensure_import_paths, path_hack_disabled


def test_path_hack_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("FINAINCE_NO_PATH_HACK", raising=False)
    assert path_hack_disabled() is False
    monkeypatch.setenv("FINAINCE_NO_PATH_HACK", "1")
    assert path_hack_disabled() is True
    assert ensure_import_paths() == []


def test_reproduction_extra_lists_both_engines() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text()
    assert 'reproduction = [' in text
    assert '"reproagent"' in text
    assert '"aiminer"' in text
    assert "FINAINCE_NO_PATH_HACK" in Path(__file__).resolve().parents[1].joinpath(
        "src/finaince/_paths.py"
    ).read_text()

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
    assert "reproagent @ git+https://github.com/WeissHymmnos/ReproAgent.git@finaince-desk" in text
    assert "aiminer @ git+https://github.com/WeissHymmnos/aiminer.git@finaince-312" in text
    assert "FINAINCE_NO_PATH_HACK" in Path(__file__).resolve().parents[1].joinpath(
        "src/finaince/_paths.py"
    ).read_text()


def test_packaging_312_uses_public_git_extras() -> None:
    """The isolated 3.12 job must install from public extras, not a private token."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "packaging-312.yml").read_text()
    pyproject = (root / "pyproject.toml").read_text()
    assert "FINAINCE_NO_PATH_HACK" in workflow
    assert "SIBLING_CHECKOUT_TOKEN" not in workflow
    assert "if [ -d ../reproagent ]" not in workflow
    assert "if [ -d ../aiminer ]" not in workflow
    assert "uv pip install -e \".[reproduction]\"" in workflow
    assert "from aiminer.manager import cull_alpha_pool" in workflow
    assert "print('ok'" in workflow
    assert "git+https://github.com/WeissHymmnos/ReproAgent.git@finaince-desk" in pyproject
    assert "git+https://github.com/WeissHymmnos/aiminer.git@finaince-312" in pyproject

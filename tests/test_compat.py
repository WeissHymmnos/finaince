"""Cross-platform process and path helpers."""

from __future__ import annotations

from pathlib import Path

from finaince.compat import (
    conda_env_roots,
    current_pgid,
    pid_alive,
    popen_detached,
    python_in_env,
    terminate_process_tree,
)
from finaince.runtime import aiminer_python, default_pdf_root, documents_root


def test_documents_root_finds_sibling_trees() -> None:
    root = documents_root()
    assert (root / "aiminer" / "src").is_dir()
    assert (root / "reproagent" / "src").is_dir()


def test_default_pdf_root_is_layout_relative_not_hardcoded_home() -> None:
    root = default_pdf_root()
    # Must be under the sibling workspace or $HOME/Documents — never a baked-in user path
    # that only exists on the original author's machine.
    text = str(root)
    assert "KnowledgeBase" in text
    assert not text.startswith("/home/wh/") or str(documents_root()).startswith("/home/wh/")


def test_python_in_env_finds_posix_and_windows_layouts(tmp_path: Path) -> None:
    posix = tmp_path / "posix"
    (posix / "bin").mkdir(parents=True)
    (posix / "bin" / "python").write_text("", encoding="utf-8")
    assert python_in_env(posix) == posix / "bin" / "python"

    win = tmp_path / "win"
    (win / "Scripts").mkdir(parents=True)
    (win / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    assert python_in_env(win) == win / "Scripts" / "python.exe"


def test_aiminer_python_honors_env_override(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "custom-python"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("AIMINER_PYTHON", str(fake))
    assert Path(aiminer_python()) == fake


def test_conda_env_roots_include_windows_localappdata(monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    roots = [str(p) for p in conda_env_roots("aiminer")]
    assert any("Local" in r and "miniconda3" in r and "aiminer" in r for r in roots)


def test_pid_alive_current_process() -> None:
    import os

    assert pid_alive(os.getpid()) is True
    assert pid_alive(2**30) is False


def test_pid_alive_false_for_exited_child() -> None:
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    proc.wait()
    assert pid_alive(proc.pid) is False


def test_popen_detached_posix_starts_new_session(monkeypatch) -> None:
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        class _P:
            pid = 9
        return _P()

    monkeypatch.setattr("finaince.compat.is_windows", lambda: False)
    monkeypatch.setattr("finaince.compat.subprocess.Popen", fake_popen)
    popen_detached(["true"])
    assert captured.get("start_new_session") is True


def test_popen_detached_windows_uses_new_process_group(monkeypatch) -> None:
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        class _P:
            pid = 9
        return _P()

    monkeypatch.setattr("finaince.compat.is_windows", lambda: True)
    monkeypatch.setattr("finaince.compat.subprocess.Popen", fake_popen)
    monkeypatch.setattr("finaince.compat.subprocess.CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    popen_detached(["true"])
    assert captured.get("creationflags", 0) & 0x200
    assert "start_new_session" not in captured


def test_terminate_windows_falls_back_to_taskkill(monkeypatch) -> None:
    calls: list[list[str]] = []

    def boom(*_a, **_k):
        raise OSError("no ctrl-break")

    monkeypatch.setattr("finaince.compat.is_windows", lambda: True)
    monkeypatch.setattr("finaince.compat.os.kill", boom)
    monkeypatch.setattr(
        "finaince.compat.subprocess.run",
        lambda cmd, **_k: calls.append(list(cmd)),
    )
    terminate_process_tree(4242, 4242)
    assert calls
    assert calls[0][:4] == ["taskkill", "/PID", "4242", "/T"]


def test_current_pgid_does_not_require_getpgid(monkeypatch) -> None:
    monkeypatch.setattr("finaince.compat.os.getpgid", None, raising=False)
    assert current_pgid(17) == 17

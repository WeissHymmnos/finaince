"""OS differences: process trees and interpreter locations."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any


def is_windows() -> bool:
    return os.name == "nt"


def current_pgid(pid: int | None = None) -> int:
    pid = os.getpid() if pid is None else int(pid)
    getter = getattr(os, "getpgid", None)
    if getter is None:
        return pid
    try:
        return int(getter(pid))
    except OSError:
        return pid


def popen_detached(argv: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    """Start a child that cancel() can reap without killing this process."""
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    if is_windows():
        flags = int(kwargs.get("creationflags") or 0)
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        kwargs["creationflags"] = flags
        kwargs.pop("start_new_session", None)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def pid_alive(pid: int) -> bool:
    """True if ``pid`` is a live (non-zombie) process."""
    pid = int(pid)
    waitpid = getattr(os, "waitpid", None)
    if waitpid is not None:
        try:
            waited, _status = waitpid(pid, int(getattr(os, "WNOHANG", 0)))
            if waited == pid:
                return False
        except (ChildProcessError, ProcessLookupError, OSError):
            pass
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            text = proc_stat.read_text(encoding="utf-8")
            state = text.rsplit(")", 1)[-1].split()[0]
            return state not in {"Z", "X"}
        except (OSError, IndexError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def terminate_process_tree(pid: int, pgid: int | None = None) -> None:
    """Best-effort kill of a job and its children (POSIX group or Windows tree)."""
    pid = int(pid)
    if is_windows():
        ctrl = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl is not None:
            try:
                os.kill(pid, ctrl)
                return
            except (OSError, ValueError, SystemError):
                pass
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    target = int(pgid or pid)
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        try:
            killpg(target, signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def python_in_env(root: Path) -> Path | None:
    """Return python executable under a venv/conda prefix, if present."""
    for parts in (
        ("Scripts", "python.exe"),
        ("Scripts", "python"),
        ("bin", "python.exe"),
        ("bin", "python"),
        ("bin", "python3"),
        ("python.exe",),
    ):
        cand = root.joinpath(*parts)
        if cand.is_file():
            return cand
    return None


def conda_env_roots(name: str = "aiminer") -> list[Path]:
    roots: list[Path] = []
    prefix = os.getenv("CONDA_PREFIX")
    if prefix and Path(prefix).name.lower() == name.lower():
        roots.append(Path(prefix))
    home = Path.home()
    roots.extend(
        [
            home / ".conda" / "envs" / name,
            home / "miniconda3" / "envs" / name,
            home / "anaconda3" / "envs" / name,
            home / "miniforge3" / "envs" / name,
            home / "mambaforge" / "envs" / name,
            Path("/opt/conda/envs") / name,
        ]
    )
    local = os.getenv("LOCALAPPDATA")
    if local:
        roots.extend(
            [
                Path(local) / "miniconda3" / "envs" / name,
                Path(local) / "anaconda3" / "envs" / name,
                Path(local) / "miniforge3" / "envs" / name,
            ]
        )
    programdata = os.getenv("PROGRAMDATA")
    if programdata:
        roots.append(Path(programdata) / "miniconda3" / "envs" / name)
    return roots

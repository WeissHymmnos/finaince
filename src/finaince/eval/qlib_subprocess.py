"""Optional 3.10 AlphaEval via AIMINER_PYTHON. Never invents a successful qlib run."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def qlib_subprocess_enabled() -> bool:
    return os.environ.get("FINAINCE_QLIB_SUBPROCESS", "").strip() == "1"


def aiminer_src() -> Path:
    from finaince._paths import documents_root

    return documents_root() / "aiminer" / "src"


def child_qlib_eval(req: dict[str, Any]) -> dict[str, Any]:
    """In-process unit that the child module also runs. Tests must call this."""
    from aiminer.core.qlib_child import run_request

    return run_request(req)


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(aiminer_src())
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + prev if prev else "")
    return env


def run_qlib_eval(
    expression: str,
    *,
    start: str | None = None,
    end: str | None = None,
    universe: str | None = None,
    python: str | None = None,
    data_backend: str | None = None,
    local_data_path: str | None = None,
) -> dict[str, Any]:
    """Spawn AIMINER_PYTHON -m aiminer.core.qlib_child with aiminer/src on PYTHONPATH."""
    from finaince.runtime import aiminer_python

    explicit = (os.getenv("AIMINER_PYTHON") or os.getenv("FINAINCE_AIMINER_PYTHON") or "").strip()
    py = (python or explicit or aiminer_python() or "").strip()
    if not py or not Path(py).expanduser().is_file():
        return {
            "ok": False,
            "error": "qlib_subprocess_missing_python",
            "error_type": "MissingInterpreter",
            "metrics": {},
        }
    payload = {
        "expression": expression,
        "start": start,
        "end": end,
        "universe": universe,
        "data_backend": data_backend
        or os.environ.get("FINAINCE_QLIB_BACKEND")
        or os.environ.get("FINAINCE_DATA_SOURCE")
        or "qlib",
        "local_data_path": local_data_path
        or os.environ.get("LOCAL_DATA_PATH")
        or os.environ.get("AIMINER_LOCAL_DATA_PATH"),
        "local_data_layout": "panel"
        if str(
            local_data_path
            or os.environ.get("LOCAL_DATA_PATH")
            or ""
        ).endswith((".parquet", ".pq", ".csv"))
        else "auto",
    }
    try:
        timeout = float(os.environ.get("FINAINCE_QLIB_TIMEOUT", "120") or 120)
        proc = subprocess.run(
            [str(Path(py).expanduser()), "-m", "aiminer.core.qlib_child"],
            input=json.dumps(payload, default=str),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
            cwd=str(aiminer_src().parent),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "metrics": {},
        }
    text = (proc.stdout or "").strip()
    if not text:
        err = (proc.stderr or "").strip() or f"exit_{proc.returncode}"
        return {"ok": False, "error": err, "error_type": "EmptyPayload", "metrics": {}}
    try:
        body = json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid_json",
            "error_type": "InvalidPayload",
            "metrics": {},
        }
    if not isinstance(body, dict):
        return {"ok": False, "error": "invalid_payload", "error_type": "InvalidPayload", "metrics": {}}
    if proc.returncode != 0:
        body["ok"] = False
        body.setdefault("error", f"exit_{proc.returncode}")
        body.setdefault("error_type", "SubprocessFailed")
    body.setdefault("metrics", {})
    if body.get("ok") is True and not expression.strip():
        body["ok"] = False
        body["error"] = "empty_expression"
    return body


def compare_engines(
    expression: str,
    *,
    start: str | None = None,
    end: str | None = None,
    data_backend: str = "local",
    universe: str | None = None,
) -> dict[str, Any]:
    """repro_polars vs optional qlib subprocess. Never silent ok=true."""
    from finaince.eval.router import EvalRequest, evaluate

    left = evaluate(
        EvalRequest(
            expression=expression,
            dialect="repro_polars",
            data_backend=data_backend,
            start=start,
            end=end,
            universe=universe or "local_panel",
        )
    )
    left_dump = {
        "ok": left.ok,
        "error": left.error,
        "metrics": left.metrics,
        "dialect": left.dialect,
    }
    if not qlib_subprocess_enabled():
        return {
            "ok": False,
            "skipped": True,
            "error": "qlib_subprocess_disabled",
            "repro_polars": left_dump,
            "qlib": None,
        }
    right = evaluate(
        EvalRequest(
            expression=expression,
            dialect="qlib",
            data_backend=data_backend,
            start=start,
            end=end,
            universe=universe or "local_panel",
        )
    )
    right_dump = {
        "ok": right.ok,
        "error": right.error,
        "metrics": right.metrics,
        "dialect": right.dialect,
    }
    return {
        "ok": bool(left.ok and right.ok),
        "skipped": False,
        "error": None if (left.ok and right.ok) else (right.error or left.error),
        "repro_polars": left_dump,
        "qlib": right_dump,
    }

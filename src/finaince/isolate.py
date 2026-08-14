"""Isolated factor implementation: frozen stdlib (+ optional installed numeric libs).

No pip, no network, no subprocess from inside the child.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

FROZEN_MODULES = frozenset(
    {
        "math",
        "statistics",
        "json",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "operator",
        "decimal",
        "typing",
        "numpy",
        "pandas",
        "polars",
    }
)
_BLOCKED_PREFIXES = ("subprocess", "socket", "ctypes", "multiprocessing", "pathlib", "importlib")


def isolator_available() -> dict[str, Any]:
    py = Path(sys.executable)
    if not py.is_file():
        return {"ok": False, "via": None, "error": "missing_python"}
    try:
        completed = subprocess.run(
            [str(py), "-c", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "via": "subprocess", "error": str(exc)}
    if completed.returncode != 0:
        return {"ok": False, "via": "subprocess", "error": (completed.stderr or "spawn_failed")[:200]}
    return {"ok": True, "via": "subprocess", "python": str(py)}


def child_isolate(req: dict[str, Any]) -> dict[str, Any]:
    """Shipped child: exec user source against a tiny panel. Tests must call this."""
    source = str(req.get("source") or "")
    if not source.strip():
        return {"ok": False, "error": "empty_source"}
    lowered = source.lower()
    if "pip install" in lowered or "pip.main" in lowered:
        return {"ok": False, "error": "pip_forbidden"}
    panel = req.get("panel") or default_panel()
    ns: dict[str, Any] = {"__name__": "isolated_factor", "panel": panel, "__builtins__": _frozen_builtins()}
    try:
        compiled = compile(source, "<isolated>", "exec")
        exec(compiled, ns, ns)  # noqa: S102 — intentional, frozen child
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    values = _invoke_compute(ns, panel)
    if isinstance(values, dict) and values.get("error"):
        return {"ok": False, "error": str(values["error"])}
    if not isinstance(values, list) or len(values) < 3:
        return {"ok": False, "error": "compute_must_return_numeric_list"}
    returns = {f"2024-01-{i:02d}": float(v) for i, v in enumerate(values, start=1)}
    ic = _naive_ic(values)
    return {
        "ok": True,
        "name": str(ns.get("NAME") or req.get("name") or "isolated"),
        "daily_returns": returns,
        "ic": ic,
        "n": len(values),
    }


def _frozen_import(name: str, *args: Any, **kwargs: Any):
    root = name.split(".", 1)[0]
    if root not in FROZEN_MODULES or any(name.startswith(p) for p in _BLOCKED_PREFIXES):
        raise ImportError(f"module not allowed in isolator: {name}")
    return __import__(name, *args, **kwargs)


def _frozen_builtins() -> dict[str, Any]:
    safe = dict(__builtins__) if isinstance(__builtins__, dict) else dict(vars(__builtins__))
    safe["__import__"] = _frozen_import
    return safe


def default_panel() -> dict[str, list[float]]:
    close = [100.0 + i * 0.4 + ((-1) ** i) * 0.2 for i in range(12)]
    return {"close": close, "open": [c - 0.1 for c in close]}


def run_isolated(source: str, *, name: str = "isolated", panel: dict[str, Any] | None = None) -> dict[str, Any]:
    """Spawn a child that only runs ``child_isolate``. No pip in the child."""
    avail = isolator_available()
    if not avail.get("ok"):
        return {"ok": False, "skipped": True, "error": "isolator_unavailable", "reason": avail.get("error")}
    payload = json.dumps({"source": source, "name": name, "panel": panel or default_panel()}, default=str)
    env = os.environ.copy()
    env["FINAINCE_ISOLATE"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "finaince.isolate"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "skipped": True, "error": str(exc)}
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "isolate_child_failed")[:400]
        return {"ok": False, "error": err}
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return {"ok": False, "error": f"isolate_bad_json: {exc}", "raw": completed.stdout[:400]}


def upsert_isolated(result: dict[str, Any], *, universe: str = "local_panel") -> dict[str, Any]:
    """Catalog upsert for a successful isolated impl. Gates stay fail-closed on the row."""
    from datetime import UTC, datetime

    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import (
        FactorExpression,
        FactorLineage,
        FactorMetrics,
        FactorRecord,
    )

    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "isolate_failed"}
    now = datetime.now(UTC)
    source_ref = f"iso_{abs(hash(result.get('name'))) % 10**10}"
    rec = FactorRecord(
        id=f"fac_{source_ref}",
        name=str(result.get("name") or "isolated"),
        expression=FactorExpression(dialect="python_sandbox", text="compute(panel)", validated=True),
        universe=universe,
        metrics=FactorMetrics(ic=result.get("ic")),
        daily_returns=dict(result.get("daily_returns") or {}),
        status="candidate",
        lineage=FactorLineage(source="manual", source_ref=source_ref, engine_db="isolate"),
        tags=["isolated", "python_sandbox"],
        created_at=now,
        updated_at=now,
    )
    stored = FactorCatalog().upsert(rec)
    return {"ok": True, "catalog_id": stored.id, "record": stored}


def _invoke_compute(ns: dict[str, Any], panel: dict[str, Any]) -> Any:
    fn = ns.get("compute")
    if not callable(fn):
        return {"error": "missing_compute"}
    try:
        out = fn(panel)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    if isinstance(out, dict):
        try:
            return [float(v) for v in out.values()]
        except (TypeError, ValueError):
            return {"error": "compute_dict_not_numeric"}
    try:
        return [float(v) for v in list(out)]
    except (TypeError, ValueError):
        return {"error": "compute_not_numeric"}


def _naive_ic(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    fwd = [values[i] - values[i - 1] for i in range(1, len(values))]
    mean = sum(fwd) / len(fwd)
    var = sum((x - mean) ** 2 for x in fwd) / len(fwd)
    if var <= 0:
        return 0.0
    # Rank-ish: correlation of level vs next increment, bounded.
    levels = values[:-1]
    lm = sum(levels) / len(levels)
    num = sum((a - lm) * (b - mean) for a, b in zip(levels, fwd, strict=False))
    den_a = sum((a - lm) ** 2 for a in levels) ** 0.5
    den_b = sum((b - mean) ** 2 for b in fwd) ** 0.5
    if den_a == 0 or den_b == 0:
        return 0.0
    corr = num / (den_a * den_b)
    return float(max(-1.0, min(1.0, corr)))


def main() -> None:
    raw = sys.stdin.read()
    req = json.loads(raw or "{}")
    print(json.dumps(child_isolate(req), default=str))


if __name__ == "__main__":
    main()

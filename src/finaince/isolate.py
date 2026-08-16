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


_FORBIDDEN_SOURCE = ("open(", "eval(", "exec(", "compile(", "__import__(")


def child_isolate(req: dict[str, Any]) -> dict[str, Any]:
    """Shipped child: exec user source against a tiny panel. Tests must call this."""
    source = str(req.get("source") or "")
    if not source.strip():
        return {"ok": False, "error": "empty_source"}
    lowered = source.lower()
    if "pip install" in lowered or "pip.main" in lowered:
        return {"ok": False, "error": "pip_forbidden"}
    if any(token in source for token in _FORBIDDEN_SOURCE):
        return {"ok": False, "error": "forbidden_builtin"}
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
    return {
        "ok": True,
        "name": str(ns.get("NAME") or req.get("name") or "isolated"),
        "expression": str(ns.get("EXPRESSION") or req.get("expression") or "").strip() or None,
        "values": values,
        "n": len(values),
        "daily_returns": {},
        "ic": None,
    }


def _frozen_import(name: str, *args: Any, **kwargs: Any):
    root = name.split(".", 1)[0]
    if root not in FROZEN_MODULES or any(name.startswith(p) for p in _BLOCKED_PREFIXES):
        raise ImportError(f"module not allowed in isolator: {name}")
    return __import__(name, *args, **kwargs)


_SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozenset",
    "int",
    "isinstance",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "range",
    "reversed",
    "round",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
)


def _deny_builtin(name: str):
    def _blocked(*_a: Any, **_k: Any) -> None:
        raise PermissionError(f"{name} is not allowed in isolator")

    _blocked.__name__ = name
    return _blocked


def _frozen_builtins() -> dict[str, Any]:
    root = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    safe: dict[str, Any] = {name: root[name] for name in _SAFE_BUILTIN_NAMES if name in root}
    safe["__import__"] = _frozen_import
    safe["__build_class__"] = root["__build_class__"]
    safe["__name__"] = "isolated_factor"
    for banned in ("open", "eval", "exec", "compile", "input", "breakpoint"):
        safe[banned] = _deny_builtin(banned)
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
        out = {"ok": False, "error": err}
        return _remember_isolate(out, name=name)
    try:
        parsed = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        parsed = {"ok": False, "error": f"isolate_bad_json: {exc}", "raw": completed.stdout[:400]}
    return _remember_isolate(parsed, name=name)


def error_prefix(error: str | None) -> str:
    text = (error or "").strip()
    if not text:
        return ""
    return text.split(":", 1)[0].strip()[:80]


def similar_errors(error: str | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
    """Retrieve prior isolate/eval errors with the same class/prefix from the real chain."""
    from finaince.trace import list_chain

    prefix = error_prefix(error)
    hits: list[dict[str, Any]] = []
    for ev in list_chain(limit=80):
        ev_err = str(ev.get("error") or "")
        if not ev_err:
            continue
        if prefix and error_prefix(ev_err) != prefix and not ev_err.startswith(prefix):
            continue
        hits.append(
            {
                "id": ev.get("id"),
                "action": ev.get("action"),
                "error": ev_err,
                "summary": ev.get("summary"),
            }
        )
        if len(hits) >= max(1, int(limit)):
            break
    return hits


def _remember_isolate(result: dict[str, Any], *, name: str) -> dict[str, Any]:
    try:
        from finaince.trace import append_event

        append_event(
            "isolated_impl",
            metrics={"ok": result.get("ok"), "name": name},
            error=None if result.get("ok") else str(result.get("error") or "isolate_failed"),
            summary=f"isolate {name} ok={result.get('ok')}",
        )
    except Exception:
        pass
    result["similar_errors"] = similar_errors(None if result.get("ok") else str(result.get("error") or ""))
    return result


def upsert_isolated(result: dict[str, Any], *, universe: str = "local_panel") -> dict[str, Any]:
    """Catalog upsert after isolate. IC/returns come from shipped eval, not list indices."""
    from datetime import UTC, datetime

    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import (
        FactorExpression,
        FactorLineage,
        FactorMetrics,
        FactorRecord,
        finite_ic,
    )

    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "isolate_failed"}
    now = datetime.now(UTC)
    source_ref = f"iso_{abs(hash(result.get('name'))) % 10**10}"
    expression = str(result.get("expression") or "").strip()
    ic = None
    returns: dict[str, float] = {}
    simulated = True
    if expression:
        from finaince.eval.router import EvalRequest, evaluate

        ev = evaluate(
            EvalRequest(expression=expression, dialect="repro_polars", universe=universe)
        )
        if ev.ok:
            ic = finite_ic((ev.metrics or {}).get("ic_mean"))
            raw = (ev.metrics or {}).get("daily_returns") or {}
            if isinstance(raw, dict):
                returns = {str(k): float(v) for k, v in raw.items()}
            simulated = False
    rec = FactorRecord(
        id=f"fac_{source_ref}",
        name=str(result.get("name") or "isolated"),
        expression=FactorExpression(
            dialect="repro_polars" if expression else "python_sandbox",
            text=expression or "compute(panel)",
            validated=bool(expression),
        ),
        universe=universe,
        metrics=FactorMetrics(ic=ic),
        daily_returns=returns,
        status="candidate",
        lineage=FactorLineage(source="manual", source_ref=source_ref, engine_db="isolate"),
        tags=["isolated", "python_sandbox"],
        is_simulated=simulated,
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

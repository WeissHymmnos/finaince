"""Isolated factor implementation: frozen stdlib (+ optional installed numeric libs).

No pip, no network, no subprocess from inside the child.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from finaince._swallow import log_swallowed

FROZEN_MODULES = frozenset(
    {
        "math",
        "statistics",
        "json",
        "datetime",
        "collections",
        "itertools",
        "functools",
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


# --- WS-E: OS-level bubblewrap layer above the frozen-builtins child ---------


def sandbox_mode() -> str:
    raw = (os.environ.get("FINAINCE_SANDBOX") or "auto").strip().lower()
    return raw if raw in {"auto", "bwrap", "off"} else "auto"


def bwrap_available() -> bool:
    import shutil

    return shutil.which("bwrap") is not None


def sandbox_backend() -> dict[str, Any]:
    mode = sandbox_mode()
    available = bwrap_available()
    active = "bwrap" if (mode == "bwrap" or (mode == "auto" and available)) else "frozen_builtin"
    return {
        "mode": mode,
        "bwrap_available": available,
        "active": active,
        "layers": ["frozen_builtin"] + (["bwrap"] if available else []),
    }


def _bwrap_binds() -> list[str]:
    import finaince

    candidates = {str(Path(sys.prefix).resolve())}
    try:
        candidates.add(str(Path(sys.executable).resolve().parent.parent))
    except OSError:
        pass
    package_parent = str(Path(finaince.__file__).resolve().parent.parent)
    candidates.add(package_parent)
    return sorted(candidates)


def _child_command() -> list[str]:
    return [sys.executable, "-m", "finaince.isolate"]


def _bwrap_command() -> list[str]:
    argv = ["bwrap", "--unshare-all"]
    for bind in _bwrap_binds():
        argv += ["--ro-bind", bind, bind]
    argv += [
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--new-session",
        "--die-with-parent",
        "--",
        *_child_command(),
    ]
    return argv


class ASTSecurityError(PermissionError):
    """Raised when source code violates sandboxed AST whitelist constraints."""


ALLOWED_NODE_TYPES = frozenset(
    {
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Return,
        ast.Pass,
        ast.Expr,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.If,
        ast.IfExp,
        ast.For,
        ast.While,
        ast.Break,
        ast.Continue,
        ast.ListComp,
        ast.DictComp,
        ast.SetComp,
        ast.GeneratorExp,
        ast.comprehension,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Call,
        ast.keyword,
        ast.Attribute,
        ast.Subscript,
        ast.Slice,
        ast.Name,
        ast.Constant,
        ast.FormattedValue,
        ast.JoinedStr,
        ast.Lambda,
        ast.NamedExpr,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Set,
        ast.Starred,
        ast.Load,
        ast.Store,
        ast.Del,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.MatMult,
        ast.LShift,
        ast.RShift,
        ast.BitOr,
        ast.BitXor,
        ast.BitAnd,
        ast.Invert,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
    }
)

FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "__builtins__",
        "help",
        "exit",
        "quit",
        "memoryview",
        "sys",
        "os",
        "subprocess",
        "socket",
        "ctypes",
        "shutil",
        "importlib",
        "operator",
        "attrgetter",
        "methodcaller",
        "itemgetter",
    }
)

FORBIDDEN_ATTRS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "system",
        "popen",
        "spawn",
        "kill",
        "remove",
        "unlink",
        "sys",
        "os",
        "subprocess",
        "modules",
        "ctypes",
        "ctypeslib",
        "f2py",
        "environ",
        "socket",
        "shutil",
        "run",
        "call",
        "check_output",
        "check_call",
        "load_library",
        "CDLL",
        "cdll",
        "listdir",
        "fork",
        "execl",
        "execv",
        "attrgetter",
        "methodcaller",
        "itemgetter",
        "read_csv",
        "read_parquet",
        "read_json",
        "read_table",
        "read_excel",
        "to_csv",
        "to_parquet",
        "to_json",
        "load",
        "save",
        "fromfile",
    }
)


class QuantASTValidator(ast.NodeVisitor):
    """Enforce strict whitelist syntax checks against factor computation AST."""

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in ALLOWED_NODE_TYPES:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or (node.names[0].name if node.names else "")
                raise ImportError(f"module import is forbidden in sandboxed factor environment: {mod}")
            lineno = getattr(node, "lineno", "?")
            raise ASTSecurityError(
                f"Forbidden AST node type: {type(node).__name__} at line {lineno}"
            )
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        mod = node.names[0].name if node.names else ""
        raise ImportError(f"module import is forbidden in sandboxed factor environment: {mod}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or (node.names[0].name if node.names else "")
        raise ImportError(f"module import is forbidden in sandboxed factor environment: {mod}")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise ASTSecurityError(f"Access to dunder identifier '{node.id}' is forbidden")
        if node.id in FORBIDDEN_NAMES:
            raise ASTSecurityError(f"Access to dangerous identifier '{node.id}' is forbidden")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ASTSecurityError(f"Access to private/dunder attribute '{node.attr}' is forbidden")
        if node.attr in FORBIDDEN_ATTRS or node.attr in FORBIDDEN_NAMES:
            raise ASTSecurityError(f"Access to dangerous attribute '{node.attr}' is forbidden")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            if node.slice.value.startswith("__"):
                raise ASTSecurityError(f"Access to dunder key '{node.slice.value}' is forbidden")
        elif isinstance(node.slice, ast.Tuple):
            for elt in node.slice.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value.startswith("__"):
                    raise ASTSecurityError(f"Access to dunder key '{elt.value}' is forbidden")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            if node.value.startswith("__"):
                raise ASTSecurityError(f"Dunder string constant '{node.value}' is forbidden in sandbox")
            lowered = node.value.lower()
            if "pip install" in lowered or "pip.main" in lowered:
                raise ASTSecurityError("pip install is forbidden in sandbox")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith("__"):
            raise ASTSecurityError(f"Dunder function name '{node.name}' is forbidden")
        if node.decorator_list:
            raise ASTSecurityError("Decorators are forbidden in sandboxed factor environment")
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.arg.startswith("__"):
                raise ASTSecurityError(f"Dunder argument name '{arg.arg}' is forbidden")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.arg.startswith("__"):
                raise ASTSecurityError(f"Dunder argument name '{arg.arg}' is forbidden")
        self.generic_visit(node)

    def validate(self, tree: ast.AST) -> None:
        """Validate an AST against the whitelist policy, raising on violations."""
        self.visit(tree)


def validate_factor_ast(source: str) -> ast.AST:
    """Parse and validate factor source code via AST visitor before compilation."""
    tree = ast.parse(source, filename="<isolated>")
    QuantASTValidator().validate(tree)
    return tree


def child_isolate(req: dict[str, Any]) -> dict[str, Any]:
    """Shipped child: exec user source against a tiny panel. Tests must call this."""
    source = str(req.get("source") or "")
    if not source.strip():
        return {"ok": False, "error": "empty_source"}
    lowered = source.lower()
    if "pip install" in lowered or "pip.main" in lowered:
        return {"ok": False, "error": "pip_forbidden"}
    try:
        tree = validate_factor_ast(source)
    except (ImportError, ASTSecurityError, SyntaxError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    panel = req.get("panel") or default_panel()
    ns = _safe_namespace(panel)
    try:
        compiled = compile(tree, "<isolated>", "exec")
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
    "pow",
    "range",
    "reversed",
    "round",
    "set",
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
    for banned in (
        "open",
        "eval",
        "exec",
        "compile",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
    ):
        safe[banned] = _deny_builtin(banned)
    return safe


def default_panel() -> dict[str, list[float]]:
    close = [100.0 + i * 0.4 + ((-1) ** i) * 0.2 for i in range(12)]
    return {"close": close, "open": [c - 0.1 for c in close]}


def _safe_namespace(panel: dict[str, Any] | None = None) -> dict[str, Any]:
    import types

    panel_data = default_panel() if panel is None else panel
    ns: dict[str, Any] = {
        "__name__": "isolated_factor",
        "panel": panel_data,
        "__builtins__": _frozen_builtins(),
    }
    for mod_name in (
        "math",
        "json",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "decimal",
    ):
        try:
            ns[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    try:
        import statistics as _stat

        ns["statistics"] = types.SimpleNamespace(
            mean=_stat.mean,
            median=_stat.median,
            median_grouped=_stat.median_grouped,
            median_high=_stat.median_high,
            median_low=_stat.median_low,
            mode=_stat.mode,
            multimode=_stat.multimode,
            stdev=_stat.stdev,
            variance=_stat.variance,
            pstdev=_stat.pstdev,
            pvariance=_stat.pvariance,
            quantiles=_stat.quantiles,
            geometric_mean=_stat.geometric_mean,
            harmonic_mean=_stat.harmonic_mean,
            correlation=_stat.correlation,
            covariance=_stat.covariance,
            linear_regression=_stat.linear_regression,
            fmean=_stat.fmean,
            NormalDist=_stat.NormalDist,
        )
    except ImportError:
        pass

    for mod_name, aliases in (
        ("numpy", ("np", "numpy")),
        ("pandas", ("pd", "pandas")),
        ("polars", ("pl", "polars")),
    ):
        try:
            mod = __import__(mod_name)
            for alias in aliases:
                ns[alias] = mod
        except ImportError:
            pass

    return ns


def run_isolated(
    source: str,
    *,
    name: str = "isolated",
    panel: dict[str, Any] | None = None,
    expression: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Spawn a child that only runs ``child_isolate``. No pip in the child."""
    avail = isolator_available()
    if not avail.get("ok"):
        return {"ok": False, "skipped": True, "error": "isolator_unavailable", "reason": avail.get("error")}
    payload = json.dumps(
        {
            "source": source,
            "name": name,
            "panel": panel or default_panel(),
            "expression": expression,
        },
        default=str,
    )
    env = os.environ.copy()
    env["FINAINCE_ISOLATE"] = "1"
    mode = sandbox_mode()
    avail_bwrap = bwrap_available()
    use_bwrap = avail_bwrap and mode in {"auto", "bwrap"}
    attempts: list[tuple[str, list[str]]] = []
    if use_bwrap:
        attempts.append(("bwrap", _bwrap_command()))
    attempts.append(("frozen_builtin", _child_command()))
    last_error: str | None = "bwrap_unavailable" if (mode == "bwrap" and not avail_bwrap) else None
    deadline = time.monotonic() + max(0.1, float(timeout))
    for layer, argv in attempts:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"ok": False, "skipped": True, "via": layer, "error": f"isolate_timeout: exceeded {timeout}s"}
        try:
            completed = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                text=True,
                timeout=max(0.1, remaining),
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"isolate_timeout: {exc}"
            return {"ok": False, "skipped": True, "via": layer, "error": last_error}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if layer == "bwrap":
                continue
            return {"ok": False, "skipped": True, "via": layer, "error": last_error}
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "isolate_child_failed")[:400]
            if layer == "bwrap":
                last_error = f"bwrap_layer_failed:{err[:200]}"
                continue
            out = {"ok": False, "via": layer, "error": err}
            if use_bwrap or mode == "bwrap":
                out["sandbox_fallback"] = True
                out["sandbox_fallback_reason"] = last_error or "bwrap_unavailable"
            return _remember_isolate(out, name=name)
        try:
            parsed = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            parsed = {"ok": False, "error": f"isolate_bad_json: {exc}", "raw": completed.stdout[:400]}
        parsed["via"] = layer
        if layer == "frozen_builtin" and (use_bwrap or mode == "bwrap"):
            parsed["sandbox_fallback"] = True
            parsed["sandbox_fallback_reason"] = last_error or "bwrap_unavailable"
        return _remember_isolate(parsed, name=name)
    return {"ok": False, "skipped": True, "error": last_error or "isolate_unreachable"}


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
    except Exception as _exc:  # noqa: BLE001
        log_swallowed("finaince.isolate:675", _exc)
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
    if not expression:
        return {"ok": False, "error": "not_scored", "catalog_id": None}
    from finaince.eval.router import EvalRequest, evaluate
    from finaince.loop import _pit_window

    window = _pit_window()
    if window is not None:
        ev = evaluate(
            EvalRequest(
                expression=expression,
                dialect="repro_polars",
                data_backend="pit",
                universe=universe,
                start=window[0],
                end=window[1],
                cost_bps=5.0,
            )
        )
    else:
        ev = evaluate(EvalRequest(expression=expression, dialect="repro_polars", universe=universe))
    if not ev.ok:
        return {"ok": False, "error": ev.error or "not_scored", "catalog_id": None}
    metrics = ev.metrics or {}
    ic = finite_ic(metrics.get("ic", metrics.get("ic_mean")))
    raw = metrics.get("daily_returns") or {}
    returns = {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    rec = FactorRecord(
        id=f"fac_{source_ref}",
        name=str(result.get("name") or "isolated"),
        expression=FactorExpression(
            dialect="repro_polars",
            text=expression,
            validated=True,
        ),
        universe=universe,
        engine=str(metrics.get("engine") or "engine_native"),
        window_start=metrics.get("window_start"),
        window_end=metrics.get("window_end"),
        citable=bool(metrics.get("citable")),
        turnover_model=metrics.get("turnover_model"),
        metrics=FactorMetrics(
            ic=ic,
            rank_ic=finite_ic(metrics.get("rank_ic")),
            icir=finite_ic(metrics.get("icir")),
            rank_icir=finite_ic(metrics.get("rank_icir")),
            sharpe=finite_ic(metrics.get("sharpe_ratio")),
            sharpe_net=finite_ic(metrics.get("sharpe_net")),
            cost_bps=finite_ic(metrics.get("cost_bps")),
        ),
        daily_returns=returns,
        status="candidate",
        lineage=FactorLineage(source="manual", source_ref=source_ref, engine_db="isolate"),
        tags=["isolated", "python_sandbox"],
        is_simulated=False,
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


def main() -> None:
    raw = sys.stdin.read()
    req = json.loads(raw or "{}")
    print(json.dumps(child_isolate(req), default=str))


if __name__ == "__main__":
    main()

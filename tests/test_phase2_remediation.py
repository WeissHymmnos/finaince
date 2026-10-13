"""Comprehensive Phase 2 remediation E2E & acceptance test suite across Tiers 1-4.

Requirements Covered:
- R1: SQLite Connection Full Lifecycle & File Descriptor Leak Prevention ([P1-10])
- R2: FastMCP Async Offloading & Event Loop Decoupling ([P1-12])
- R3: Sandbox AST Whitelist Security Hardening ([P1-01])
- R4: Factor Rolling Window Pure Vectorization Acceleration ([P2-09])

Test Hierarchy:
- Tier 1: Feature Coverage & Interface Contracts (Happy Paths)
- Tier 2: Boundary & Corner Cases
- Tier 3: Cross-Feature Interactions & Adversarial Attack Suites
- Tier 4: Real-World Workloads & Performance Benchmarks
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import math
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

# Optional/Contract imports with graceful fallback for pre-implementation AST / DB contracts
try:
    from finaince.db import ensure_database_initialized, managed_connection
except ImportError:
    managed_connection = None  # type: ignore[assignment]
    ensure_database_initialized = None  # type: ignore[assignment]

try:
    from finaince.isolate import (
        ASTSecurityError,
        QuantASTValidator,
        validate_factor_ast,
    )
except ImportError:
    ASTSecurityError = PermissionError  # type: ignore[assignment,misc]
    QuantASTValidator = None  # type: ignore[assignment]
    validate_factor_ast = None  # type: ignore[assignment]


# ==============================================================================
# Helper fixtures and synthetic data generators
# ==============================================================================


def _count_open_fds() -> int:
    """Return the exact count of open file descriptors in the current process."""
    if os.path.exists("/proc/self/fd"):
        return len(os.listdir("/proc/self/fd"))
    # Fallback for non-Linux platforms if ever encountered
    import resource

    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def _make_factor_panel(
    start_date: date,
    num_days: int,
    codes: list[str],
    *,
    string_dates: bool = False,
    inject_nulls: bool = False,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[Any, set[str]]]:
    """Generate aligned scores, forward returns, and daily point-in-time universe."""
    rng = np.random.default_rng(42)
    scores_rows = []
    fwd_rows = []
    universe_by_day: dict[Any, set[str]] = {}

    for d_idx in range(num_days):
        current_date = start_date + timedelta(days=d_idx)
        # Skip weekends
        if current_date.weekday() >= 5:
            continue
        key_date: Any = current_date.isoformat() if string_dates else current_date
        universe_by_day[key_date] = set(codes)

        for c_idx, code in enumerate(codes):
            score_val = float(rng.normal(0.0, 1.0))
            fwd_ret = float(rng.normal(0.0005 + 0.001 * score_val, 0.02))

            # Null injection for boundary testing
            score_entry: float | None = score_val
            fwd_entry: float | None = fwd_ret
            if inject_nulls and (d_idx + c_idx) % 17 == 0:
                score_entry = None
            if inject_nulls and (d_idx + c_idx) % 23 == 0:
                fwd_entry = None

            scores_rows.append({
                "trade_date": key_date,
                "ts_code": code,
                "_score": score_entry,
            })
            fwd_rows.append({
                "trade_date": key_date,
                "ts_code": code,
                "_fwd_ret": fwd_entry,
            })

    scores_df = pl.DataFrame(scores_rows)
    fwd_df = pl.DataFrame(fwd_rows)
    return scores_df, fwd_df, universe_by_day


# ==============================================================================
# R1: SQLite Connection Full Lifecycle & Descriptor Leak Prevention (P1-10)
# ==============================================================================


class TestR1SQLiteConnectionLifecycle:
    """R1: Managed connection context manager, WAL mode, pragmas, zero FD leak, and hot-path DDL elimination."""

    def test_r1_tier1_managed_connection_lifecycle_and_pragmas(self, tmp_path: Path) -> None:
        """Tier 1: Verify managed_connection context manager entry/exit, explicit close, and WAL pragmas."""
        if managed_connection is None:
            pytest.fail(
                "Defect [P1-10]: managed_connection is not implemented in finaince.db. "
                "Centralized connection lifecycle manager with guaranteed close() is required."
            )

        db_path = tmp_path / "lifecycle_test.db"

        # 1. Enter context manager: test open connection and pragmas
        with managed_connection(db_path) as conn:
            assert isinstance(conn, sqlite3.Connection)
            # Verify busy_timeout pragma
            timeout_row = conn.execute("PRAGMA busy_timeout;").fetchone()
            assert timeout_row is not None and timeout_row[0] == 30000, (
                f"PRAGMA busy_timeout expected 30000, got {timeout_row}"
            )

            # Verify synchronous pragma (1 = NORMAL)
            sync_row = conn.execute("PRAGMA synchronous;").fetchone()
            assert sync_row is not None and sync_row[0] in (1, 2), (
                f"PRAGMA synchronous expected NORMAL(1) or FULL(2), got {sync_row}"
            )

            # Verify foreign keys enabled
            fk_row = conn.execute("PRAGMA foreign_keys;").fetchone()
            assert fk_row is not None and fk_row[0] == 1

            # Insert sample data to test transaction commit
            conn.execute("CREATE TABLE test_data (id INT PRIMARY KEY, val TEXT);")
            conn.execute("INSERT INTO test_data VALUES (1, 'hello');")

        # 2. Verify connection is explicitly closed on exit (cannot execute on closed conn)
        with pytest.raises(sqlite3.ProgrammingError, match="(?i)closed"):
            conn.execute("SELECT 1;")

        # 3. Verify changes were committed by clean exit
        with managed_connection(db_path) as conn2:
            row = conn2.execute("SELECT val FROM test_data WHERE id=1;").fetchone()
            assert row is not None and row[0] == "hello"

        # 4. Verify transaction rollback on exception inside context
        try:
            with managed_connection(db_path) as conn3:
                conn3.execute("INSERT INTO test_data VALUES (2, 'rollback_me');")
                raise ValueError("Simulated operational failure inside transaction")
        except ValueError:
            pass

        # Verify row 2 was NOT committed
        with managed_connection(db_path) as conn4:
            row2 = conn4.execute("SELECT val FROM test_data WHERE id=2;").fetchone()
            assert row2 is None, "Transaction was not rolled back upon exception!"

    def test_r1_tier1_ensure_database_initialized_idempotence(self, tmp_path: Path) -> None:
        """Tier 1: ensure_database_initialized executes DDL once and is strictly idempotent."""
        if ensure_database_initialized is None:
            pytest.fail(
                "Defect [P1-10]: ensure_database_initialized not implemented in finaince.db. "
                "DDL execution must be centralized and cached per process."
            )

        db_path = tmp_path / "init_test.db"
        ddl = """
        CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, count INT);
        CREATE INDEX IF NOT EXISTS idx_demo_count ON demo(count);
        """

        # Call multiple times: should not raise and should set WAL mode
        ensure_database_initialized(db_path, ddl)
        ensure_database_initialized(db_path, ddl)
        ensure_database_initialized(db_path, ddl)

        with sqlite3.connect(str(db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert str(mode).lower() == "wal", f"Expected WAL mode, got {mode}"
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            assert "demo" in tables

    def test_r1_tier2_1000_operations_zero_fd_leak(self, tmp_path: Path) -> None:
        """Tier 2: 1000 consecutive read/write operations with gc.disable() asserts zero FD leak (delta_fd <= 1)."""
        from finaince.catalog.store import FactorCatalog

        db_path = tmp_path / "catalog_leak_test.db"
        cat = FactorCatalog(db_path)

        from datetime import UTC, datetime

        from finaince.domain.factor import (
            FactorExpression,
            FactorLineage,
            FactorMetrics,
            FactorRecord,
        )

        now = datetime.now(UTC)
        record = FactorRecord(
            id="leak_probe_factor",
            name="leak_probe_factor",
            expression=FactorExpression(dialect="repro_polars", text="Rank(close)"),
            universe="csi300_pit",
            metrics=FactorMetrics(ic=0.04),
            lineage=FactorLineage(source="discovery", source_ref="probe_ref"),
            created_at=now,
            updated_at=now,
        )
        cat.upsert(record)

        # Disable garbage collection to expose any unclosed file handles
        gc.disable()
        try:
            initial_fds = _count_open_fds()

            # Execute 1000 consecutive read and write operations
            for i in range(1000):
                if i % 2 == 0:
                    cat.get("leak_probe_factor")
                else:
                    cat.find_by_expr_hash("dummy_hash", "repro_polars")

            final_fds = _count_open_fds()
            delta_fd = final_fds - initial_fds

            assert delta_fd <= 1, (
                f"Defect [P1-10]: File descriptor leak detected! "
                f"FD count increased by {delta_fd} over 1000 operations (initial={initial_fds}, final={final_fds}). "
                "Connections must be explicitly closed via managed_connection."
            )
        finally:
            gc.enable()
            gc.collect()

    def test_r1_tier2_hot_path_no_ddl(self, tmp_path: Path) -> None:
        """Tier 2: Hot read path find_by_expr_hash must NOT execute DDL (CREATE INDEX) on repeated queries."""
        import inspect

        from finaince.catalog.store import FactorCatalog

        source = inspect.getsource(FactorCatalog.find_by_expr_hash)
        assert "CREATE INDEX" not in source, (
            "Defect [P1-10]: FactorCatalog.find_by_expr_hash still contains inline 'CREATE INDEX' DDL! "
            "DDL on hot read path triggers exclusive schema locks and invalidates query plans."
        )


# ==============================================================================
# R2: FastMCP Async Offloading & Event Loop Decoupling (P1-12)
# ==============================================================================


class TestR2FastMCPAsyncEventLoop:
    """R2: FastMCP tools and serve endpoints async execution, event loop non-blocking, and nested loop safety."""

    def test_r2_tier1_fastmcp_tools_async_signature_and_execution(self) -> None:
        """Tier 1: All FastMCP tools in CUSTOM_TOOLS are coroutine functions and execute without blocking."""
        import finaince.sdk_ext as sdk

        assert len(sdk.CUSTOM_TOOLS) == 15, f"Expected 15 FastMCP tools, got {len(sdk.CUSTOM_TOOLS)}"

        # Every custom tool handler must be a coroutine function (async def)
        for t in sdk.CUSTOM_TOOLS:
            assert inspect.iscoroutinefunction(t.handler), (
                f"Tool '{t.name}' handler is not a coroutine function (async def)!"
            )

        # Execute validate_expression_tool in an active event loop
        async def _run_tool() -> dict[str, Any]:
            validate_tool = next(t for t in sdk.CUSTOM_TOOLS if t.name == "validate_expression")
            return await validate_tool.handler({"expression": "Rank(close)"})

        res = asyncio.run(_run_tool())
        assert isinstance(res, dict)
        assert "content" in res

    def test_r2_tier1_serve_endpoints_async_offload(self) -> None:
        """Tier 1: Heavy FastAPI routes in serve.py are coroutine functions offloading blocking tasks."""
        from finaince.serve import create_app

        app = create_app()
        heavy_paths = {"/api/v1/eval", "/api/v1/reproduce", "/api/v1/agent", "/api/v1/bench", "/api/v1/impl", "/api/v1/loop"}

        found_paths = set()
        for route in app.routes:
            route_path = getattr(route, "path", "")
            if route_path in heavy_paths:
                found_paths.add(route_path)
                endpoint = getattr(route, "endpoint", None)
                assert endpoint is not None
                assert inspect.iscoroutinefunction(endpoint), (
                    f"Defect [P1-12]: Route '{route_path}' endpoint {endpoint.__name__} is NOT async def! "
                    "Heavy computation and disk I/O endpoints must be async def and offload to threadpool."
                )

        assert heavy_paths.issubset(found_paths), f"Missing expected heavy routes: {heavy_paths - found_paths}"

    def test_r2_tier2_nested_event_loop_safe_async_execution(self) -> None:
        """Tier 2: Safe async execution under existing running event loop without raising RuntimeError."""
        import finaince.agent as agent_mod

        # Attempt to discover _safe_run_async helper if provided
        safe_runner = getattr(agent_mod, "_safe_run_async", None)

        async def _nested_caller() -> Any:
            async def _dummy_coro() -> str:
                await asyncio.sleep(0.001)
                return "safe_success"

            if safe_runner is not None:
                # If helper is available, directly verify it runs without RuntimeError: asyncio.run()
                return safe_runner(_dummy_coro)

            # Verify run_research_desk handles running loop without unhandled RuntimeError
            return agent_mod.run_research_desk("query in running loop", max_turns=1)

        result = asyncio.run(_nested_caller())
        assert result is not None
        if isinstance(result, dict) and "error_type" in result:
            assert result["error_type"] != "RuntimeError" or "running event loop" not in str(result.get("error")), (
                "Defect [P1-12]: Crashed with 'RuntimeError: asyncio.run() cannot be called from a running event loop'!"
            )

    def test_r2_tier3_health_check_latency_during_heavy_workload(self) -> None:
        """Tier 3: Concurrent health check probe latency during heavy computation must be strictly < 20ms."""
        import httpx

        from finaince.serve import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)

        async def _run_concurrent_benchmark() -> list[float]:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Background heavy factor/matrix calculation simulation
                async def _heavy_worker() -> None:
                    # Offloaded heavy calculation
                    def _cpu_work() -> float:
                        total = 0.0
                        for i in range(1_000_000):
                            total += math.sqrt(i)
                        return total

                    await asyncio.to_thread(_cpu_work)

                heavy_task = asyncio.create_task(_heavy_worker())

                # Send 5 health check requests during the heavy computation
                latencies = []
                for _ in range(5):
                    t0 = time.perf_counter()
                    resp = await client.get("/api/v1/health")
                    elapsed = time.perf_counter() - t0
                    assert resp.status_code == 200
                    latencies.append(elapsed)
                    await asyncio.sleep(0.002)

                await heavy_task
                return latencies

        latencies = asyncio.run(_run_concurrent_benchmark())
        max_latency = max(latencies)
        assert max_latency < 0.020, (
            f"Defect [P1-12]: Health check latency spiked to {max_latency * 1000:.2f}ms (threshold is 20ms)! "
            "Event loop was blocked by heavy background workload."
        )


# ==============================================================================
# R3: Sandbox AST Whitelist Security Hardening (P1-01)
# ==============================================================================


class TestR3SandboxASTSecurity:
    """R3: Default-deny AST whitelist syntax validation and adversarial attack defense."""

    def test_r3_tier1_ast_whitelist_allows_valid_quant_factors(self) -> None:
        """Tier 1: AST whitelist allows valid quant factor formulas across Polars, Pandas, Numpy, and math."""
        from finaince.isolate import child_isolate

        valid_sources = [
            # 1. Pure Python math & list comprehension
            (
                "pure_python",
                """
def compute(panel):
    close = list(panel["close"])
    return [0.0] + [(close[i] - close[i - 1]) / (close[i - 1] or 1.0) for i in range(1, len(close))]
""",
            ),
            # 2. Vectorized Polars expression
            (
                "polars_vectorized",
                """
def compute(panel):
    df = pl.DataFrame(panel)
    return df.select((pl.col("close") - pl.col("close").shift(1)) / pl.col("close").shift(1))["close"].fill_null(0.0).to_list()
""",
            ),
            # 3. Pandas Series pct_change
            (
                "pandas_series",
                """
def compute(panel):
    s = pd.Series(panel["close"])
    return s.pct_change().fillna(0.0).tolist()
""",
            ),
            # 4. Numpy array diff
            (
                "numpy_diff",
                """
def compute(panel):
    arr = np.array(panel["close"], dtype=float)
    diff = np.diff(arr)
    return [0.0] + list(diff / arr[:-1])
""",
            ),
        ]

        for name, src in valid_sources:
            if validate_factor_ast is not None:
                validate_factor_ast(src)

            res = child_isolate({"source": src, "name": name})
            assert res["ok"] is True, f"Legitimate quant factor '{name}' failed sandbox execution: {res.get('error')}"
            assert isinstance(res.get("values"), list)
            assert len(res["values"]) >= 3

    def test_r3_tier1_ast_whitelist_blocks_forbidden_syntax_nodes(self) -> None:
        """Tier 1: AST whitelist rejects forbidden AST node types (classes, async, with, globals)."""
        from finaince.isolate import child_isolate

        forbidden_node_payloads = [
            ("class_def", "class Evil: pass\ndef compute(panel): return [1.0, 2.0, 3.0]"),
            ("async_func", "async def compute(panel): return [1.0, 2.0, 3.0]"),
            ("with_stmt", "with None: pass\ndef compute(panel): return [1.0, 2.0, 3.0]"),
            ("global_stmt", "global g_val\ndef compute(panel): return [1.0, 2.0, 3.0]"),
            ("yield_stmt", "def compute(panel): yield 1.0"),
        ]

        for label, payload in forbidden_node_payloads:
            if validate_factor_ast is not None:
                with pytest.raises((ASTSecurityError, SyntaxError, PermissionError)):
                    validate_factor_ast(payload)

            res = child_isolate({"source": payload})
            assert res["ok"] is False, f"Defect [P1-01]: Forbidden syntax construct '{label}' was allowed into sandbox!"

    def test_r3_tier2_empty_and_whitespace_inputs(self) -> None:
        """Tier 2: Empty or whitespace-only code strings reject gracefully with error='empty_source'."""
        from finaince.isolate import child_isolate

        for empty_val in ("", "   ", "\n\t  \n"):
            res = child_isolate({"source": empty_val})
            assert res["ok"] is False
            assert res.get("error") == "empty_source"

    def test_r3_tier2_syntax_errors_handled_gracefully(self) -> None:
        """Tier 2: Code with syntax errors reports failure without unhandled process termination."""
        from finaince.isolate import child_isolate

        bad_syntax = "def compute(panel): return [1, 2, (3 + 4]"
        res = child_isolate({"source": bad_syntax})
        assert res["ok"] is False
        assert "syntaxerror" in str(res.get("error")).lower()

    def test_r3_tier2_complex_nested_formulas_and_chained_methods(self) -> None:
        """Tier 2: Complex nested mathematical expressions and chained Polars/Pandas methods pass validation."""
        from finaince.isolate import child_isolate

        nested_code = """
def compute(panel):
    close = panel["close"]
    # Complex nested mathematical arithmetic with math functions
    res = [
        ((math.sqrt(abs(x) + 1.0) * 2.5 - 1.2) / (math.log(abs(x) + 2.0) + 0.1)) ** 2
        for x in close
    ]
    return res
"""
        res = child_isolate({"source": nested_code})
        assert res["ok"] is True, f"Complex nested math rejected: {res.get('error')}"

    def test_r3_tier3_adversarial_subclasses_object_tree_crawling(self) -> None:
        """Tier 3: Attack crawling object subclasses tree via ().__class__.__mro__[1].__subclasses__() is blocked."""
        from finaince.isolate import child_isolate

        attack_payload = """
def compute(panel):
    for c in ().__class__.__mro__[1].__subclasses__():
        try:
            g = c.__init__.__globals__
            if 'os' in g:
                return [1.0, 2.0, float(g['os'].getpid())]
        except Exception:
            pass
    return [1.0, 2.0, 3.0]
"""
        if validate_factor_ast is not None:
            with pytest.raises((ASTSecurityError, PermissionError)):
                validate_factor_ast(attack_payload)

        res = child_isolate({"source": attack_payload})
        assert res["ok"] is False, (
            "Defect [P1-01]: Sandbox escape succeeded! "
            "Object subclasses crawling via ().__class__.__mro__[1].__subclasses__() returned ok=True."
        )

    def test_r3_tier3_adversarial_globals_and_builtins_access(self) -> None:
        """Tier 3: Direct reference to globals(), vars(), locals(), or __builtins__ is blocked."""
        from finaince.isolate import child_isolate

        attacks = [
            "def compute(panel): return [float(len(globals())) for _ in range(3)]",
            "def compute(panel): return [float(len(vars())) for _ in range(3)]",
            "def compute(panel): return [float(len(__builtins__)) for _ in range(3)]",
            "def compute(panel): return [float(len(dir())) for _ in range(3)]",
        ]

        for payload in attacks:
            if validate_factor_ast is not None:
                with pytest.raises((ASTSecurityError, PermissionError)):
                    validate_factor_ast(payload)

            res = child_isolate({"source": payload})
            assert res["ok"] is False, f"Defect [P1-01]: Introspection access allowed: {payload}"

    def test_r3_tier3_adversarial_reflection_functions(self) -> None:
        """Tier 3: Reflection functions getattr, setattr, delattr, hasattr are strictly forbidden."""
        from finaince.isolate import child_isolate

        attacks = [
            "def compute(panel): getattr(math, 'sin')(1.0); return [1.0, 2.0, 3.0]",
            "def compute(panel): setattr(panel, 'x', 1.0); return [1.0, 2.0, 3.0]",
            "def compute(panel): delattr(panel, 'x'); return [1.0, 2.0, 3.0]",
            "def compute(panel): hasattr(math, 'sin'); return [1.0, 2.0, 3.0]",
        ]

        for payload in attacks:
            if validate_factor_ast is not None:
                with pytest.raises((ASTSecurityError, PermissionError)):
                    validate_factor_ast(payload)

            res = child_isolate({"source": payload})
            assert res["ok"] is False, f"Defect [P1-01]: Reflection call allowed: {payload}"

    def test_r3_tier3_adversarial_dynamic_eval_exec_and_evasions(self) -> None:
        """Tier 3: Dynamic code execution (eval, exec, compile) including whitespace/aliasing evasions is blocked."""
        from finaince.isolate import child_isolate

        evasion_attacks = [
            # Whitespace between identifier and call
            "def compute(panel):\n    eval   ('1+1')\n    return [1.0, 2.0, 3.0]",
            "def compute(panel):\n    exec   ('a = 1')\n    return [1.0, 2.0, 3.0]",
            # Aliasing via list container
            "def compute(panel):\n    f = [eval][0]\n    return [float(f('1+2')) for _ in range(3)]",
            # Open call with whitespace
            "def compute(panel):\n    open  ('/etc/passwd')\n    return [1.0, 2.0, 3.0]",
        ]

        for payload in evasion_attacks:
            if validate_factor_ast is not None:
                with pytest.raises((ASTSecurityError, PermissionError)):
                    validate_factor_ast(payload)

            res = child_isolate({"source": payload})
            assert res["ok"] is False, f"Defect [P1-01]: Dynamic eval/exec evasion allowed: {payload}"

    def test_r3_tier3_adversarial_transitive_module_globals_escape(self) -> None:
        """Tier 3: Transitive escape via allowed module globals (e.g. json.loads.__globals__) is blocked."""
        from finaince.isolate import child_isolate

        attack_payload = """
import json
def compute(panel):
    b = json.loads.__globals__["__builtins__"]
    fn = b["eval"]
    return fn("[1.0, 2.0, 3.0]")
"""
        if validate_factor_ast is not None:
            with pytest.raises((ASTSecurityError, PermissionError, ImportError)):
                validate_factor_ast(attack_payload)

        res = child_isolate({"source": attack_payload})
        assert res["ok"] is False, (
            "Defect [P1-01]: Transitive escape via json.loads.__globals__ succeeded, returned ok=True!"
        )

    def test_r3_tier3_adversarial_imports_rejected_with_importerror(self) -> None:
        """Tier 3: Arbitrary module imports (socket, os, sys, subprocess) are rejected with ImportError."""
        from finaince.isolate import child_isolate

        import_attacks = [
            "import socket\ndef compute(panel): return [1.0, 2.0, 3.0]",
            "import os\ndef compute(panel): return [1.0, 2.0, 3.0]",
            "import sys\ndef compute(panel): return [1.0, 2.0, 3.0]",
            "from subprocess import Popen\ndef compute(panel): return [1.0, 2.0, 3.0]",
        ]

        for payload in import_attacks:
            res = child_isolate({"source": payload})
            assert res["ok"] is False
            assert "importerror" in str(res.get("error")).lower() or "module" in str(res.get("error")).lower()

    def test_r3_tier3_adversarial_subsystem_exploits_blocked(self) -> None:
        """Tier 3: Transitive subsystem escapes and implicit file I/O payloads are blocked with ASTSecurityError or ImportError."""
        from finaince.isolate import ASTSecurityError, child_isolate, validate_factor_ast

        exploit_payloads = [
            ("statistics_sys_subprocess", "def compute(panel): sub = statistics.sys.modules['subprocess']; return [1.0, 2.0, 3.0]"),
            ("numpy_f2py_subprocess", "def compute(panel): sub = np.f2py.subprocess; return [1.0, 2.0, 3.0]"),
            ("numpy_ctypeslib_ctypes", "def compute(panel): libc = np.ctypeslib.ctypes.CDLL(None); return [1.0, 2.0, 3.0]"),
            ("polars_os_listdir", "def compute(panel): files = pl.os.listdir('.'); return [1.0, 2.0, 3.0]"),
            ("operator_attrgetter_globals", "def compute(panel): g = operator.attrgetter('__globals__')(json.loads); return [1.0, 2.0, 3.0]"),
            ("polars_read_csv", "def compute(panel): df = pl.read_csv('/etc/passwd'); return [1.0, 2.0, 3.0]"),
        ]

        for label, payload in exploit_payloads:
            if validate_factor_ast is not None:
                with pytest.raises((ASTSecurityError, PermissionError, ImportError)):
                    validate_factor_ast(payload)

            res = child_isolate({"source": payload, "name": label})
            assert res["ok"] is False, f"Exploit payload '{label}' was allowed into sandbox: {res}"
            err = str(res.get("error", "")).lower()
            assert any(token in err for token in ("astsecurityerror", "importerror", "permissionerror")), (
                f"Exploit '{label}' did not raise security error: {res.get('error')}"
            )


# ==============================================================================
# R4: Factor Rolling Window Vectorization & Performance (P2-09)
# ==============================================================================


class TestR4FactorVectorizationPerformance:
    """R4: Vectorized IC, RankIC, layered long-short, turnover churn, corner cases, and benchmark < 1.0s."""

    def test_r4_tier1_vectorized_daily_ic_spearman_and_pearson(self) -> None:
        """Tier 1: _daily_ic calculates Pearson and Spearman Rank IC producing valid bounded floats in [-1.0, 1.0]."""
        from finaince.data_track import _daily_ic

        codes = [f"0000{i:02d}.SZ" for i in range(25)]
        scores, forwards, u_by_day = _make_factor_panel(date(2023, 1, 3), 40, codes)

        pearson_ics = _daily_ic(scores, forwards, u_by_day, spearman=False)
        spearman_ics = _daily_ic(scores, forwards, u_by_day, spearman=True)

        assert len(pearson_ics) > 0, "Pearson IC series is empty"
        assert len(spearman_ics) > 0, "Spearman IC series is empty"

        for ic_val in pearson_ics:
            assert math.isfinite(ic_val)
            assert -1.0 <= ic_val <= 1.0

        for rank_ic_val in spearman_ics:
            assert math.isfinite(rank_ic_val)
            assert -1.0 <= rank_ic_val <= 1.0

    def test_r4_tier1_vectorized_layered_long_short_returns_and_turnover(self) -> None:
        """Tier 1: _layered_long_short calculates quintile LS returns and daily membership churn."""
        from finaince.data_track import _layered_long_short, _window_metrics

        codes = [f"6000{i:02d}.SH" for i in range(30)]
        scores, forwards, u_by_day = _make_factor_panel(date(2023, 1, 3), 35, codes)

        series, turnover_by_day = _layered_long_short(scores, forwards, u_by_day, n_groups=5)

        assert len(series) > 0, "Long-short returns series is empty"
        for trade_date, ret in series:
            assert math.isfinite(ret)
            assert -1.0 <= ret <= 1.0

        assert len(turnover_by_day) > 0, "Turnover series is empty"
        for t_day, churn in turnover_by_day.items():
            assert math.isfinite(churn)
            assert 0.0 <= churn <= 2.0

        metrics = _window_metrics(series, cost_bps=5.0, turnover_by_day=turnover_by_day)
        assert "sharpe_net" in metrics or "sharpe" in metrics
        assert "max_drawdown" in metrics
        assert "turnover" in metrics
        sharpe_val = metrics.get("sharpe_net") if "sharpe_net" in metrics else metrics.get("sharpe")
        assert sharpe_val is not None and math.isfinite(sharpe_val)
        assert -1.0 <= metrics["max_drawdown"] <= 0.0

    def test_r4_tier2_sparse_suspension_trading_days(self) -> None:
        """Tier 2: Trading days with stock suspensions or active count < n_groups * 4 skip and reset turnover."""
        from finaince.data_track import _layered_long_short

        codes = [f"600{i:03d}.SH" for i in range(25)]
        scores, forwards, u_by_day = _make_factor_panel(date(2023, 1, 3), 30, codes)

        # Force day 10 to have only 5 stocks (below n_groups * 4 = 20 threshold)
        all_days = sorted(u_by_day.keys())
        sparse_day = all_days[10]
        u_by_day[sparse_day] = set(codes[:5])

        series, turnover_by_day = _layered_long_short(scores, forwards, u_by_day, n_groups=5)

        # The sparse day must be skipped from the long-short return series
        series_days = {d for d, _ in series}
        assert sparse_day not in series_days, f"Sparse day {sparse_day} was not skipped!"

        # Churn should reset on next day and not raise key or index errors
        assert len(series) > 0

    def test_r4_tier2_single_stock_and_small_universe(self) -> None:
        """Tier 2: Single-stock universe (below minimum threshold) returns empty metrics without crashing."""
        from finaince.data_track import _daily_ic, _layered_long_short

        codes = ["000001.SZ"]
        scores, forwards, u_by_day = _make_factor_panel(date(2023, 1, 3), 15, codes)

        # _daily_ic requires >= 10 stocks: must return []
        ics = _daily_ic(scores, forwards, u_by_day, spearman=False)
        assert ics == []

        # _layered_long_short requires >= n_groups * 4: must return ([], {})
        series, turnover = _layered_long_short(scores, forwards, u_by_day, n_groups=5)
        assert series == []
        assert turnover == {}

    def test_r4_tier2_nan_and_null_handling(self) -> None:
        """Tier 2: Nulls and NaNs in scores and returns are dropped cleanly without corrupting results."""
        from finaince.data_track import _daily_ic, _layered_long_short

        codes = [f"600{i:03d}.SH" for i in range(30)]
        scores, forwards, u_by_day = _make_factor_panel(
            date(2023, 1, 3), 30, codes, inject_nulls=True
        )

        ics = _daily_ic(scores, forwards, u_by_day, spearman=True)
        assert len(ics) > 0
        for v in ics:
            assert math.isfinite(v)

        series, _ = _layered_long_short(scores, forwards, u_by_day, n_groups=5)
        assert len(series) > 0
        for _, ls_ret in series:
            assert math.isfinite(ls_ret)

    def test_r4_tier2_date_types_compatibility_date_vs_string(self) -> None:
        """Tier 2: Factors with pl.Date and pl.String trade_date evaluate seamlessly without type mismatch."""
        from finaince.data_track import _daily_ic, _layered_long_short

        codes = [f"000{i:03d}.SZ" for i in range(25)]

        # 1. pl.Date test
        s_date, f_date, u_date = _make_factor_panel(date(2023, 1, 3), 20, codes, string_dates=False)
        ic_date = _daily_ic(s_date, f_date, u_date, spearman=False)
        ls_date, _ = _layered_long_short(s_date, f_date, u_date, n_groups=5)
        assert len(ic_date) > 0
        assert len(ls_date) > 0

        # 2. pl.String test
        s_str, f_str, u_str = _make_factor_panel(date(2023, 1, 3), 20, codes, string_dates=True)
        ic_str = _daily_ic(s_str, f_str, u_str, spearman=False)
        ls_str, _ = _layered_long_short(s_str, f_str, u_str, n_groups=5)
        assert len(ic_str) > 0
        assert len(ls_str) > 0

    def test_r4_tier4_run_bench_performance_strictly_under_1_second(self, isolated_home: Path) -> None:
        """Tier 4: End-to-end run_bench performance benchmark asserting execution time strictly < 1.0s."""
        from finaince.data_track import run_bench
        from tests.test_data_track import _seed_cache

        # Seed synthetic cache with 2 years of CSI300 constituents
        _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)

        t0 = time.perf_counter()
        result = run_bench(
            is_start="2023-01-01",
            is_end="2023-12-31",
            oos_start="2024-01-01",
            oos_end="2024-12-31",
            cost_bps=5.0,
        )
        elapsed = time.perf_counter() - t0

        assert result.get("ok") is True, f"run_bench failed: {result.get('error')}"
        assert elapsed < 1.0, (
            f"Defect [P2-09]: run_bench took {elapsed:.3f}s, exceeding strictly < 1.0s limit! "
            "Nested O(T^2 * N) loops must be vectorized via Polars over('trade_date') expressions."
        )

    def test_r4_tier4_multi_window_factor_evaluation_workflow(self, isolated_home: Path) -> None:
        """Tier 4: Full multi-window factor evaluation produces complete metrics table across IS and OOS."""
        from finaince.data_track import run_bench
        from tests.test_data_track import _seed_cache

        _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)

        result = run_bench(
            is_start="2023-01-01",
            is_end="2023-12-31",
            oos_start="2024-01-01",
            oos_end="2024-12-31",
            cost_bps=5.0,
        )

        assert result.get("ok") is True
        table = result.get("table", [])
        assert len(table) >= 3, f"Expected at least 3 seed factors in bench table, got {len(table)}"

        for row in table:
            assert "factor" in row
            for label in ("IS", "OOS"):
                assert label in row, f"Missing window {label} in row"
                metrics = row[label]
                assert "sharpe_net" in metrics or "sharpe" in metrics
                assert "max_drawdown" in metrics
                assert "turnover" in metrics
                sharpe_val = metrics.get("sharpe_net") if "sharpe_net" in metrics else metrics.get("sharpe")
                assert sharpe_val is not None and math.isfinite(sharpe_val)
                assert -1.0 <= metrics["max_drawdown"] <= 0.0
                assert metrics["turnover"] >= 0.0

        prov = result.get("provenance", {})
        assert prov.get("universe_source") == "point_in_time"
        assert prov.get("cost_bps") == 5.0
        assert "IS" in prov.get("windows", {})
        assert "OOS" in prov.get("windows", {})

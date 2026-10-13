"""Adversarial stress harness for Phase 2 R1 & R2 verification.

Authored by Challenger P2-1 (Storage & Concurrency Adversarial Verifier).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import math
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

import finaince.jobs.runner as runner
from finaince.catalog.store import FactorCatalog
from finaince.db import ensure_database_initialized, managed_connection
from finaince.domain.factor import (
    FactorExpression,
    FactorLineage,
    FactorMetrics,
    FactorRecord,
)
from finaince.sdk_ext import CUSTOM_TOOLS, _safe_run_async
from finaince.serve import create_app


def _count_open_fds() -> int:
    """Return count of open file descriptors in current process via /proc/self/fd."""
    if os.path.exists("/proc/self/fd"):
        return len(os.listdir("/proc/self/fd"))
    import resource

    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


# ==============================================================================
# R1 Adversarial Stress Tests: File Descriptor Leaks & Concurrency
# ==============================================================================


class TestR1AdversarialStorageStress:
    """R1: FD leak stress, WAL persistence, multi-reader concurrency under heavy load."""

    def test_r1_factor_catalog_3000_ops_fd_leak_gc_disabled(self, tmp_path: Path) -> None:
        """Execute 3,000 high-frequency concurrent operations on FactorCatalog with gc.disable(). Assert delta_fd <= 1."""
        db_path = tmp_path / "catalog_3000_stress.db"
        cat = FactorCatalog(db_path)

        # Pre-populate records
        records = []
        for i in range(20):
            r = FactorRecord(
                id=f"factor_{i}",
                name=f"factor_{i}",
                expression=FactorExpression(dialect="repro_polars", text=f"Rank(close_{i})"),
                universe="csi300_pit",
                metrics=FactorMetrics(ic=0.01 * i),
                lineage=FactorLineage(source="discovery", source_ref=f"ref_{i}"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            cat.upsert(r)
            records.append(r)

        gc.disable()
        try:
            initial_fds = _count_open_fds()

            def worker_op(idx: int) -> None:
                op = idx % 6
                if op == 0:
                    cat.upsert(records[idx % len(records)])
                elif op == 1:
                    cat.get(f"factor_{idx % len(records)}")
                elif op == 2:
                    cat.find_by_expr_hash(f"Rank(close_{idx % len(records)})", "repro_polars")
                elif op == 3:
                    cat.list(source="discovery")
                elif op == 4:
                    cat.add_promotion(f"factor_{idx % len(records)}", "to_pool", "pending", {"gate": idx})
                elif op == 5:
                    cat.list_promotions()

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                futures = [executor.submit(worker_op, i) for i in range(3000)]
                for f in concurrent.futures.as_completed(futures):
                    f.result()

            final_fds = _count_open_fds()
            delta_fd = final_fds - initial_fds

            assert delta_fd <= 1, (
                f"R1 FD Leak Defect: FD count changed from {initial_fds} to {final_fds} (delta={delta_fd}) "
                f"over 3,000 concurrent FactorCatalog operations with GC disabled!"
            )
        finally:
            gc.enable()
            gc.collect()

    def test_r1_job_runner_and_catalog_combined_concurrency(self, tmp_path: Path) -> None:
        """Execute 3,000 concurrent operations combining FactorCatalog and JobRunner with gc.disable()."""
        db_path = tmp_path / "combined_3000_stress.db"
        os.environ["FINAINCE_PLATFORM_DB"] = str(db_path)
        cat = FactorCatalog(db_path)

        records = []
        for i in range(10):
            r = FactorRecord(
                id=f"factor_{i}",
                name=f"factor_{i}",
                expression=FactorExpression(dialect="repro_polars", text=f"Rank(close_{i})"),
                universe="csi300_pit",
                metrics=FactorMetrics(ic=0.01 * i),
                lineage=FactorLineage(source="discovery", source_ref=f"ref_{i}"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            cat.upsert(r)
            records.append(r)

        gc.disable()
        try:
            initial_fds = _count_open_fds()

            def combined_op(idx: int) -> None:
                op = idx % 7
                if op == 0:
                    cat.upsert(records[idx % len(records)])
                elif op == 1:
                    cat.get(f"factor_{idx % len(records)}")
                elif op == 2:
                    cat.find_by_expr_hash(f"Rank(close_{idx % len(records)})", "repro_polars")
                elif op == 3:
                    cat.list(source="discovery")
                elif op == 4:
                    runner.submit("eval", {"test": idx}, run=lambda: {"ret": idx * 2})
                elif op == 5:
                    runner.active_jobs()
                elif op == 6:
                    runner.can_submit("eval")

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = [executor.submit(combined_op, i) for i in range(3000)]
                for f in concurrent.futures.as_completed(futures):
                    f.result()

            final_fds = _count_open_fds()
            delta_fd = final_fds - initial_fds

            assert delta_fd <= 1, (
                f"R1 FD Leak Defect: FD count changed from {initial_fds} to {final_fds} (delta={delta_fd}) "
                f"over 3,000 combined Catalog & JobRunner operations with GC disabled!"
            )
        finally:
            gc.enable()
            gc.collect()

    def test_r1_sqlite_wal_persistence_and_multi_reader_concurrency(self, tmp_path: Path) -> None:
        """Verify WAL mode persists on cold reopen and multiple readers concurrently query during write transactions."""
        db_path = tmp_path / "wal_concurrency.db"
        ensure_database_initialized(db_path, "CREATE TABLE concurrency_test (id INT, val TEXT);")

        # 1. Verify cold connection has WAL mode set in database header
        with sqlite3.connect(str(db_path)) as bare_conn:
            mode = bare_conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.lower() == "wal", f"Database header is not in WAL mode: {mode}"

        # 2. Multi-reader concurrency during write
        read_counts = [0]
        stop_event = threading.Event()

        def writer() -> None:
            with managed_connection(db_path) as conn:
                for i in range(100):
                    conn.execute("INSERT INTO concurrency_test VALUES (?, ?)", (i, f"val_{i}"))
                    conn.commit()
                    time.sleep(0.002)

        def reader() -> None:
            while not stop_event.is_set():
                with managed_connection(db_path) as conn:
                    conn.execute("SELECT COUNT(*) FROM concurrency_test").fetchone()
                    read_counts[0] += 1
                    time.sleep(0.001)

        t_writer = threading.Thread(target=writer)
        t_readers = [threading.Thread(target=reader) for _ in range(6)]

        for r in t_readers:
            r.start()
        t_writer.start()

        t_writer.join()
        stop_event.set()
        for r in t_readers:
            r.join()

        assert read_counts[0] > 100, f"Expected > 100 concurrent reads, got {read_counts[0]}"


# ==============================================================================
# R2 Adversarial Stress Tests: Event Loop Responsiveness & Latency
# ==============================================================================


class TestR2AdversarialEventLoopStress:
    """R2: Event loop unblocking, probe latency < 20ms, nested loop safety."""

    def test_r2_nested_event_loop_concurrency(self) -> None:
        """Stress test 50 concurrent nested _safe_run_async invocations inside active event loop."""
        import finaince.agent as agent

        async def sample_coro(val: int) -> int:
            await asyncio.sleep(0.002)
            return val * 10

        async def nested_caller(idx: int) -> int:
            return _safe_run_async(lambda: sample_coro(idx))

        async def _run() -> None:
            tasks = [asyncio.create_task(nested_caller(i)) for i in range(50)]
            results = await asyncio.gather(*tasks)
            assert results == [i * 10 for i in range(50)]

            # Check agent.run_research_desk inside running event loop
            desk_res = agent.run_research_desk("test query in loop", max_turns=1)
            assert desk_res is not None
            if isinstance(desk_res, dict) and "error_type" in desk_res:
                assert desk_res["error_type"] != "RuntimeError" or "running event loop" not in str(desk_res.get("error"))

        asyncio.run(_run())

    def test_r2_fastmcp_endpoints_under_heavy_load(self) -> None:
        """Execute FastMCP tool handlers concurrently with heavy computation."""
        tools = {t.name: t for t in CUSTOM_TOOLS}

        async def heavy_worker() -> float:
            def _cpu() -> float:
                s = 0.0
                for i in range(2_000_000):
                    s += math.sqrt(i)
                return s

            return await asyncio.to_thread(_cpu)

        async def _run() -> None:
            heavy_task = asyncio.create_task(heavy_worker())
            for tool_name in ["validate_expression", "catalog_list", "score_factor"]:
                tool = tools[tool_name]
                if tool_name == "validate_expression":
                    res = await tool.handler({"expression": "Rank(close)"})
                elif tool_name == "catalog_list":
                    res = await tool.handler({"query": ""})
                elif tool_name == "score_factor":
                    res = await tool.handler({"metrics": {"ic": 0.05}, "factor_ic": 0.05})
                assert res is not None
            await heavy_task

        asyncio.run(_run())

    def test_r2_health_probe_latency_and_event_loop_blocking_adversarial(self) -> None:
        """Adversarial Test: Measure /api/v1/health probe response latency and event loop lag over 2.5s under heavy workload.

        Contract Requirements:
        - Response latency of health probes strictly < 20ms (target < 5ms).
        - Main event loop is NEVER blocked.
        """
        app = create_app()
        transport = httpx.ASGITransport(app=app)

        async def _run_adversarial_probe_test() -> tuple[list[float], float]:
            max_loop_lag_ms = 0.0
            stop_monitor = False

            # High-resolution event loop monitor measuring loop scheduling lag
            async def event_loop_monitor() -> None:
                nonlocal max_loop_lag_ms
                while not stop_monitor:
                    t_before = time.perf_counter()
                    await asyncio.sleep(0.005)
                    lag = (time.perf_counter() - t_before - 0.005) * 1000
                    if lag > max_loop_lag_ms:
                        max_loop_lag_ms = lag

            monitor_task = asyncio.create_task(event_loop_monitor())

            # Offloaded heavy background factor computation simulation
            async def heavy_worker() -> float:
                def _cpu() -> float:
                    total = 0.0
                    for i in range(5_000_000):
                        total += math.sqrt(i)
                    return total

                return await asyncio.to_thread(_cpu)

            heavy_task = asyncio.create_task(heavy_worker())

            latencies: list[float] = []
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                start_time = time.perf_counter()
                # Query health probes periodically across 2.5s (crossing 1.0s TTL boundary)
                while time.perf_counter() - start_time < 2.5:
                    t0 = time.perf_counter()
                    resp = await client.get("/api/v1/health")
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    assert resp.status_code == 200
                    latencies.append(elapsed_ms)
                    await asyncio.sleep(0.2)

            stop_monitor = True
            await monitor_task
            await heavy_task
            return latencies, max_loop_lag_ms

        latencies, max_lag_ms = asyncio.run(_run_adversarial_probe_test())
        max_probe_latency = max(latencies)

        assert max_probe_latency < 20.0, (
            f"Defect [P1-12]: Health check latency spiked to {max_probe_latency:.2f}ms (threshold < 20ms)! "
            f"All latencies: {[round(x, 2) for x in latencies]}. "
            f"When in-memory cache TTL (1.0s) expires, _compute_health() is invoked synchronously on the main thread, "
            f"blocking event loop and spiking probe latency."
        )

        assert max_lag_ms < 20.0, (
            f"Defect [P1-12]: Event loop was blocked for {max_lag_ms:.2f}ms (threshold < 20ms)! "
            f"Main event loop frozen during synchronous doctor_report() execution on health check route."
        )

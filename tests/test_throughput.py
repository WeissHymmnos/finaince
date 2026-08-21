"""WS-C throughput tests: panel cache + concurrency guards."""

from __future__ import annotations


def test_panel_cache_hits_within_batch(monkeypatch) -> None:
    from finaince.eval import router as router_mod
    from finaince.eval.router import EvalRequest, evaluate

    monkeypatch.setenv("FINAINCE_PANEL_CACHE", "on")
    router_mod._PANEL_CACHE.clear()
    stats_before = dict(router_mod._PANEL_CACHE_STATS)

    first = evaluate(EvalRequest(expression="Rank(close)", dialect="repro_polars", cost_bps=5.0))
    assert first.ok
    misses_after_first = router_mod._PANEL_CACHE_STATS["misses"] - stats_before["misses"]
    assert misses_after_first >= 1

    second = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars", cost_bps=5.0))
    assert second.ok
    hits_delta = router_mod._PANEL_CACHE_STATS["hits"] - stats_before["hits"]
    assert hits_delta >= 1
    assert router_mod._PANEL_CACHE_STATS["misses"] - stats_before["misses"] == misses_after_first


def test_panel_cache_invalidated_on_mtime_change(monkeypatch, tmp_path) -> None:
    import os
    from datetime import UTC, datetime

    import polars as pl

    from finaince.eval import router as router_mod

    data_dir = tmp_path / "panel"
    data_dir.mkdir()
    frame = pl.DataFrame(
        {
            "trade_date": [datetime(2023, 1, 3 + i, tzinfo=UTC).date() for i in range(2)],
            "ts_code": ["000001.SZ", "000001.SZ"],
            "close": [10.0, 11.0],
        }
    )
    parquet = data_dir / "prices.parquet"
    frame.write_parquet(parquet)
    monkeypatch.setenv("FINAINCE_LOCAL_DATA_PATH", str(data_dir))
    monkeypatch.setenv("LOCAL_DATA_PATH", str(data_dir))

    ident_a = router_mod._local_panel_identity()
    os.utime(parquet, ns=(1_000_000_000_000, 1_000_000_000_000))
    ident_b = router_mod._local_panel_identity()
    assert ident_a != ident_b


def test_can_submit_rejects_duplicate_pending(monkeypatch) -> None:

    from finaince.jobs import runner

    fake_running = {
        "id": "job_parent_1",
        "kind": "research_loop",
        "status": "running",
        "payload": {"dedup_key": "loop:x"},
    }
    monkeypatch.setattr(runner, "active_jobs", lambda kind=None: [fake_running] if kind == "research_loop" else [])
    verdict = runner.can_submit("research_loop", payload_key="loop:x")
    assert verdict["ok"] is False
    assert verdict["error"] == "duplicate_pending"
    assert verdict["running_job_id"] == "job_parent_1"
    ok_other = runner.can_submit("research_loop", payload_key="loop:y")
    assert ok_other["ok"] is True


def test_start_process_enforces_max_jobs(monkeypatch, isolated_home) -> None:
    from finaince.jobs import runner

    jobs = [
        {"id": f"j{i}", "kind": "research_loop", "status": "running", "payload": {"dedup_key": f"loop:{i}"}}
        for i in range(2)
    ]
    monkeypatch.setenv("FINAINCE_MAX_JOBS", "2")
    monkeypatch.setattr(runner, "active_jobs", lambda kind=None: jobs)
    rejected = runner.start_process("research_loop", {"dedup_key": "fresh"}, ["true"])
    assert rejected["ok"] is False
    assert rejected["error"] == "max_jobs_reached"
    assert len(rejected["running_job_ids"]) == 2

    monkeypatch.setenv("FINAINCE_MAX_JOBS", "4")
    allowed = runner.start_process(
        "research_loop",
        {"dedup_key": "fresh"},
        ["sleep", "0"],
    )
    assert allowed.get("ok") is not False

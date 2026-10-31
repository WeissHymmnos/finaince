"""Regression coverage for concurrent job updates and the audit hash chain."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from finaince.catalog import audit
from finaince.jobs import runner


def test_audit_concurrent_append_preserves_chain(isolated_home):
    audit.append("seed")
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda i: audit.append("parallel", {"index": i}), range(40)))
    assert audit.verify_tail() == {"ok": True, "count": 41}
    assert audit.verify_tail(limit=7) == {"ok": True, "count": 7}


@pytest.mark.parametrize("column,value", [("detail", '{"changed": true}'), ("prev_hash", "")])
def test_audit_detects_tampering(isolated_home, column, value):
    audit.append("first", {"value": 1})
    audit.append("second", {"value": 2})
    with audit._connect() as conn:
        conn.execute(f"UPDATE audit_log SET {column}=? WHERE id=2", (value,))
    assert audit.verify_tail()["ok"] is False


def test_fast_child_completion_is_not_overwritten(isolated_home, monkeypatch):
    def spawn(argv, *, env):
        runner.finish_job(env["FINAINCE_JOB_ID"], status="done", result={"answer": 42})
        return SimpleNamespace(pid=99999999)

    monkeypatch.setattr(runner, "popen_detached", spawn)
    job = runner.start_process("fast", {}, ["unused"])
    assert job["status"] == "done"
    assert job["result"] == {"answer": 42}
    assert runner.get_job(job["id"])["status"] == "done"


def test_late_completion_cannot_overwrite_cancelled_job(isolated_home, monkeypatch):
    monkeypatch.setattr(runner, "popen_detached", lambda *a, **kw: SimpleNamespace(pid=99999999))
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    monkeypatch.setattr(runner, "terminate_process_tree", lambda *a: None)
    job = runner.start_process("cancel", {}, ["unused"])
    assert runner.cancel(job["id"])["ok"] is True
    late = runner.finish_job(job["id"], status="done", result={"late": True})
    assert late["status"] == "cancelled"
    assert "result" not in late
    assert not audit.list_audit(action="job_finished")
    ran = []
    claimed = runner.submit("cancel", {}, job_id=job["id"], run=lambda: ran.append(True))
    assert claimed["status"] == "cancelled"
    assert not ran


def test_cancel_before_spawn_returns_terminates_child(isolated_home, monkeypatch):
    killed = []

    def spawn(argv, *, env):
        assert runner.cancel(env["FINAINCE_JOB_ID"])["ok"] is True
        return SimpleNamespace(pid=99999999)

    monkeypatch.setattr(runner, "popen_detached", spawn)
    monkeypatch.setattr(runner, "terminate_process_tree", lambda *a: killed.append(a))
    job = runner.start_process("cancel", {}, ["unused"])
    assert job["status"] == "cancelled"
    assert killed and killed[0][0] == 99999999


def test_cancel_in_process_job_cannot_kill_server(isolated_home, monkeypatch):
    killed = []
    monkeypatch.setattr(runner, "terminate_process_tree", lambda *a: killed.append(a))
    job = runner.submit("sync", {})
    result = runner.cancel(job["id"])
    assert result["ok"] is False
    assert result["error"] == "in_process_job_not_cancellable"
    assert not killed
    assert runner.get_job(job["id"])["status"] == "queued"


def test_finish_job_is_idempotent(isolated_home):
    job = runner.submit("sync", {})
    runner.finish_job(job["id"], status="done", result={"answer": 42})
    repeated = runner.finish_job(job["id"], status="error", error="late error")
    assert repeated["status"] == "done"
    assert repeated["result"] == {"answer": 42}
    assert len(audit.list_audit(action="job_finished")) == 1


@pytest.mark.parametrize("replacement", ["removed", "empty", "other_database"])
def test_schema_cache_recovers_after_database_replacement(tmp_path, replacement):
    import sqlite3

    from finaince.db import ensure_database_initialized, managed_connection

    path = tmp_path / "replaced.db"
    ddl = "CREATE TABLE IF NOT EXISTS required_table (id INTEGER)"
    ensure_database_initialized(path, ddl)
    path.unlink()
    if replacement == "empty":
        path.touch()
    elif replacement == "other_database":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
    ensure_database_initialized(path, ddl)
    with managed_connection(path) as conn:
        assert conn.execute("SELECT * FROM required_table").fetchall() == []


@pytest.mark.parametrize("result", [{"ok": False, "error": "bad factor"}, {"status": "error"},
                                   {"status": "failed", "reason": "parser failed"}])
def test_structured_failure_is_not_reported_as_completed(isolated_home, result):
    job = runner.submit("evaluate", {}, run=lambda: result)
    assert job["status"] == "error"
    assert job["error"]
    assert job["result"] == result


def test_delete_preserves_active_job_handles(isolated_home):
    job = runner.submit("sync", {})
    assert runner.delete_job(job["id"]) is False
    assert runner.get_job(job["id"]) is not None
    runner.finish_job(job["id"], status="error", error="test failure")
    assert runner.delete_job(job["id"]) is True
    assert runner.get_job(job["id"]) is None


def test_schema_cache_stays_warm_across_managed_writes_and_scale_instantiation(isolated_home):
    import json
    import time
    from datetime import UTC, datetime

    from finaince.catalog.store import FactorCatalog
    from finaince.db import managed_connection

    cat = FactorCatalog()
    now = datetime.now(UTC).isoformat()
    rows = [
        (
            f"fac_{i}",
            "discovery",
            f"ref_{i}",
            f"Factor {i}",
            None,
            "candidate",
            "repro_polars",
            "Rank(close)",
            json.dumps({"id": f"fac_{i}", "name": f"Factor {i}", "engine": "pit_core"}),
            now,
            now,
            f"hash_{i}",
        )
        for i in range(5000)
    ]
    with managed_connection(cat.db_path) as conn:
        conn.executemany(
            "INSERT INTO factor_catalog "
            "(id, source, source_ref, name, name_cn, status, dialect, formula, record_json, created_at, updated_at, expr_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    t0 = time.perf_counter()
    for _ in range(10):
        FactorCatalog(cat.db_path)
    avg_ms = ((time.perf_counter() - t0) * 1000.0) / 10.0
    assert avg_ms < 15.0

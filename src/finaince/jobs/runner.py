"""SQLite job table wrapping swarm / reproduce / eval.

Default path is in-process ``--sync``. Async jobs spawn a detached child so
cancel can reap the tree without talking to HTTP.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from finaince.compat import current_pgid, pid_alive, popen_detached, terminate_process_tree
from finaince.db import ensure_columns


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_path() -> Path:
    from finaince.settings import get_settings

    path = get_settings().platform_db
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT,
            status TEXT,
            payload_json TEXT,
            result_json TEXT,
            error TEXT,
            engine_run_id TEXT,
            pid INTEGER,
            pgid INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    ensure_columns(
        conn,
        "jobs",
        [
            ("engine_run_id", "TEXT"),
            ("pid", "INTEGER"),
            ("pgid", "INTEGER"),
        ],
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, kind)"
    )
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row | tuple) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        keys = row.keys()
        data = {k: row[k] for k in keys}
    else:
        data = {
            "id": row[0],
            "kind": row[1],
            "status": row[2],
            "error": row[3],
            "engine_run_id": row[4] if len(row) > 4 else None,
            "pid": row[5] if len(row) > 5 else None,
            "created_at": row[6] if len(row) > 6 else None,
        }
    return data


def _claimed_job_id() -> str | None:
    raw = (os.environ.get("FINAINCE_JOB_ID") or "").strip()
    return raw or None


def _load_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, kind, status, error, engine_run_id, pid, pgid, created_at, updated_at, "
            "result_json, payload_json FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    raw = data.pop("result_json", None)
    if raw:
        try:
            data["result"] = json.loads(raw)
        except json.JSONDecodeError:
            data["result"] = raw
    raw_payload = data.pop("payload_json", None)
    if raw_payload:
        try:
            data["payload"] = json.loads(raw_payload)
        except json.JSONDecodeError:
            data["payload"] = raw_payload
    return data


def finish_job(
    job_id: str,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Write a terminal status onto an existing job row and audit it."""
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, result_json=?, error=?, updated_at=? WHERE id=?",
            (
                status,
                json.dumps(result, default=str) if result is not None else None,
                error,
                _now(),
                job_id,
            ),
        )
        conn.commit()
    row = _load_job(job_id) or {}
    kind = row.get("kind")
    try:
        from finaince.catalog.audit import append as audit_append

        audit_append("job_finished", {"job_id": job_id, "kind": kind, "status": status})
    except Exception:
        pass
    try:
        from finaince.obs import emit

        emit("job_finished", kind=kind, status=status, elapsed_ms=elapsed_ms)
    except Exception:
        pass
    out = dict(row)
    if result is not None:
        out["result"] = result
    try:
        from finaince.trace import record_job_finished

        record_job_finished(out)
    except Exception:
        pass
    return out


def submit(
    kind: str,
    payload: dict[str, Any],
    *,
    run: Callable[[], Any] | None = None,
    engine_run_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Record a job and optionally run ``run`` in-process (sync).

    If ``FINAINCE_JOB_ID`` (or ``job_id``) names an existing parent row — the
    async ``start_process`` child path — this updates that row instead of
    inserting a second job the UI never polls.
    """
    claimed = (job_id or _claimed_job_id() or "").strip() or None
    existing = _load_job(claimed) if claimed else None
    job_id = claimed if existing else (claimed or uuid.uuid4().hex)
    now = _now()
    pid = os.getpid()
    pgid = current_pgid(pid)
    status = "running" if run else "queued"
    blob = json.dumps(payload, default=str)
    with _connect() as conn:
        if existing:
            conn.execute(
                "UPDATE jobs SET kind=?, status=?, payload_json=?, engine_run_id=?, pid=?, pgid=?, updated_at=? "
                "WHERE id=?",
                (kind, status, blob, engine_run_id or existing.get("engine_run_id"), pid, pgid, now, job_id),
            )
        else:
            conn.execute(
                "INSERT INTO jobs (id, kind, status, payload_json, engine_run_id, pid, pgid, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, kind, status, blob, engine_run_id, pid, pgid, now, now),
            )
        conn.commit()
    if run is None:
        return get_job(job_id, reap=False) or {"id": job_id, "kind": kind, "status": "queued"}
    started = datetime.now(UTC)
    try:
        result = run()
        final = "done"
        error = None
    except Exception as exc:  # noqa: BLE001
        result = None
        final = "error"
        error = str(exc)
    elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    return finish_job(job_id, status=final, result=result, error=error, elapsed_ms=elapsed_ms)


def max_concurrent_jobs() -> int:
    raw = (os.environ.get("FINAINCE_MAX_JOBS") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(1, value)


def active_jobs(kind: str | None = None) -> list[dict[str, Any]]:
    """Running/queued rows (SQL-filtered, status index) with dead-child reaping."""
    sql = (
        "SELECT id, kind, status, error, engine_run_id, pid, created_at FROM jobs "
        "WHERE status IN ('running','queued')"
    )
    params: list[Any] = []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        live = get_job(row[0], reap=True)
        if live and live.get("status") in {"running", "queued"}:
            out.append(live)
    return out


def can_submit(kind: str, *, payload_key: str | None = None) -> dict[str, Any]:
    """WS-C pending-aware guard: reject a second async job for the same work item."""
    for job in active_jobs(kind):
        if payload_key is not None:
            stored_payload = job.get("payload")
            stored = stored_payload.get("dedup_key") if isinstance(stored_payload, dict) else None
            if stored != payload_key:
                continue
        return {
            "ok": False,
            "error": "duplicate_pending",
            "running_job_id": job["id"],
            "running_status": job.get("status"),
        }
    return {"ok": True}


def start_process(kind: str, payload: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    """Spawn a detached child that must finish *this* job id.

    The child inherits ``FINAINCE_JOB_ID``. ``submit()`` / ``--sync`` then
    write done/error onto the same row so ``GET /jobs/{id}`` can stop.
    WS-C: duplicate-payload rejection + FINAINCE_MAX_JOBS concurrency cap.
    """
    guard = can_submit(kind, payload_key=payload.get("dedup_key") if isinstance(payload, dict) else None)
    if not guard.get("ok"):
        return guard
    running = active_jobs()
    if len(running) >= max_concurrent_jobs():
        return {
            "ok": False,
            "error": "max_jobs_reached",
            "limit": max_concurrent_jobs(),
            "running_job_ids": [job["id"] for job in running],
        }
    job_id = uuid.uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status, payload_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, kind, "queued", json.dumps(payload, default=str), now, now),
        )
        conn.commit()
    env = os.environ.copy()
    env["FINAINCE_JOB_ID"] = job_id
    try:
        proc = popen_detached(argv, env=env)
    except Exception as exc:  # noqa: BLE001
        return finish_job(job_id, status="error", error=str(exc))
    pgid = current_pgid(proc.pid)
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, engine_run_id=?, pid=?, pgid=?, updated_at=? WHERE id=?",
            ("running", str(proc.pid), proc.pid, pgid, _now(), job_id),
        )
        conn.commit()
    return get_job(job_id, reap=False) or {
        "id": job_id,
        "kind": kind,
        "status": "running",
        "pid": proc.pid,
    }


def delete_job(job_id: str) -> bool:
    """Remove a terminal job row. Returns False when the id is unknown."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
        return cur.rowcount > 0


def cancel(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"unknown job {job_id}"}
    if job.get("status") not in {"running", "queued"}:
        return {"ok": False, "error": f"job not cancellable: {job.get('status')}", "job": job}
    pid = job.get("pid")
    pgid = job.get("pgid") or pid
    if pid:
        try:
            terminate_process_tree(int(pid), int(pgid) if pgid else None)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc), "job": job}
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
            ("cancelled", _now(), job_id),
        )
        conn.commit()
    return {"ok": True, "job": get_job(job_id)}


def get_job(job_id: str, *, reap: bool = True) -> dict[str, Any] | None:
    data = _load_job(job_id)
    if not data:
        return None
    if reap and data.get("status") in {"running", "queued"}:
        pid = data.get("pid")
        if pid and not pid_alive(int(pid)):
            return finish_job(job_id, status="error", error="child_exited")
    return data


def list_jobs() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, status, error, engine_run_id, pid, created_at FROM jobs ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "id": r[0],
            "kind": r[1],
            "status": r[2],
            "error": r[3],
            "engine_run_id": r[4],
            "pid": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def run_reproduce_job(
    pdf_path: str,
    *,
    sync: bool = True,
    backtest_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from pathlib import Path

    from finaince.reproduction import reproduce_report

    payload = {"pdf_path": str(pdf_path), "backtest_kwargs": backtest_kwargs or {}, "dedup_key": f"pdf:{os.path.realpath(str(pdf_path))}"}
    extra: list[str] = []
    kwargs = dict(backtest_kwargs or {})
    if kwargs.get("start_date"):
        extra += ["--start", str(kwargs["start_date"])]
    if kwargs.get("end_date"):
        extra += ["--end", str(kwargs["end_date"])]
    if kwargs.get("data_source"):
        extra += ["--source", str(kwargs["data_source"])]
    if not sync:
        return start_process(
            "reproduce_report",
            payload,
            [sys.executable, "-m", "finaince", "reproduce", str(pdf_path), "--sync", *extra],
        )
    return submit(
        "reproduce_report",
        payload,
        run=lambda: reproduce_report(Path(pdf_path), backtest_kwargs=backtest_kwargs),
    )


def run_swarm_job(swarm_args: list[str] | None = None, *, sync: bool = True) -> dict[str, Any]:
    from finaince.discovery import run_swarm

    args = list(swarm_args or [])
    payload = {"args": args}
    engine_run_id = None
    if "--run-id" in args:
        idx = args.index("--run-id")
        if idx + 1 < len(args):
            engine_run_id = args[idx + 1]
    if not engine_run_id:
        engine_run_id = uuid.uuid4().hex[:12]
        args = ["--run-id", engine_run_id, *args]
    if not sync:
        return start_process(
            "discover_swarm",
            payload,
            [sys.executable, "-m", "finaince", "discover", "--swarm", "--sync", *args],
        )
    return submit(
        "discover_swarm",
        payload,
        run=lambda: run_swarm(args) or {"ok": True},
        engine_run_id=engine_run_id,
    )


def run_impl_job(
    source: str,
    *,
    name: str = "isolated",
    universe: str = "local_panel",
    sync: bool = True,
) -> dict[str, Any]:
    from finaince.isolate import run_isolated, upsert_isolated

    payload = {"name": name, "universe": universe}

    def _run() -> dict[str, Any]:
        import re

        match = re.search(r"^EXPRESSION\s*=\s*['\"]([^'\"]+)['\"]", source, re.M)
        expression = match.group(1) if match else None
        isolated = run_isolated(source, name=name, expression=expression)
        if not isolated.get("ok"):
            return isolated
        stored = upsert_isolated(isolated, universe=universe)
        return {**isolated, **stored}

    if not sync:
        return submit("isolated_impl", payload)
    return submit("isolated_impl", payload, run=_run)


def _loop_dedup_key() -> str:
    try:
        from finaince.eval.router import _local_panel_identity

        return "loop:" + repr(_local_panel_identity())
    except Exception:
        return "loop:unknown"


def run_loop_job(
    *,
    steps: int = 2,
    sync: bool = True,
    expressions: list[str] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    from finaince.loop import run_loop

    payload = {
        "steps": int(steps),
        "expressions": expressions,
        "workers": max(1, min(8, int(workers))),
        "dedup_key": _loop_dedup_key(),
    }
    if not sync:
        argv = [sys.executable, "-m", "finaince", "loop", "--steps", str(steps), "--sync"]
        if expressions:
            for expr in expressions:
                argv.extend(["--expression", expr])
        if workers and int(workers) > 1:
            argv.extend(["--workers", str(max(1, min(8, int(workers))))])
        return start_process(
            "research_loop",
            payload,
            argv,
        )
    return submit(
        "research_loop",
        payload,
        run=lambda: run_loop(steps=steps, expressions=expressions, workers=workers),
    )

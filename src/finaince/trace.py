"""Causal research chain: each event may cite the previous action id."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS trace_events (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    parent_id TEXT,
    job_id TEXT,
    cites TEXT,
    summary TEXT,
    error TEXT,
    metrics_json TEXT,
    extra_json TEXT,
    created_at TEXT
);
"""


def _connect() -> sqlite3.Connection:
    from finaince.catalog.store import FactorCatalog

    conn = sqlite3.connect(FactorCatalog().db_path)
    conn.executescript(_DDL)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trace_events)")}
    if "hypothesis" not in cols:
        conn.execute("ALTER TABLE trace_events ADD COLUMN hypothesis TEXT")
    return conn


def last_event() -> dict[str, Any] | None:
    rows = list_chain(limit=1)
    return rows[0] if rows else None


def append_event(
    action: str,
    *,
    parent_id: str | None = None,
    job_id: str | None = None,
    cites: str | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
    summary: str | None = None,
    extra: dict[str, Any] | None = None,
    hypothesis: str | None = None,
) -> dict[str, Any]:
    """Append one event. Default parent/cites is the previous event id."""
    prev = last_event()
    if parent_id is None and prev is not None:
        parent_id = str(prev.get("id") or "") or None
    if cites is None:
        cites = parent_id or (str(prev["job_id"]) if prev and prev.get("job_id") else None)
    event_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    slim_metrics = _slim_metrics(metrics)
    rec = {
        "id": event_id,
        "action": action,
        "parent_id": parent_id,
        "job_id": job_id,
        "cites": cites,
        "summary": summary or _default_summary(action, slim_metrics, error),
        "error": error,
        "metrics": slim_metrics,
        "extra": extra or {},
        "created_at": now,
        "hypothesis": hypothesis,
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trace_events
                (id, action, parent_id, job_id, cites, summary, error, metrics_json, extra_json, created_at, hypothesis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec["id"],
                rec["action"],
                rec["parent_id"],
                rec["job_id"],
                rec["cites"],
                rec["summary"],
                rec["error"],
                json.dumps(slim_metrics, default=str),
                json.dumps(rec["extra"], default=str),
                rec["created_at"],
                rec["hypothesis"],
            ),
        )
        conn.commit()
    return rec


def list_chain(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, action, parent_id, job_id, cites, summary, error, metrics_json, extra_json, created_at, hypothesis "
            "FROM trace_events ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        metrics: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        if row[7]:
            try:
                metrics = json.loads(row[7])
            except json.JSONDecodeError:
                metrics = {}
        if row[8]:
            try:
                extra = json.loads(row[8])
            except json.JSONDecodeError:
                extra = {}
        out.append(
            {
                "id": row[0],
                "action": row[1],
                "parent_id": row[2],
                "job_id": row[3],
                "cites": row[4],
                "summary": row[5],
                "error": row[6],
                "metrics": metrics,
                "extra": extra,
                "created_at": row[9],
                "hypothesis": row[10],
            }
        )
    return out


def recent_failures(error: str | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, action, parent_id, job_id, cites, summary, error, metrics_json, extra_json, created_at, hypothesis "
            "FROM trace_events WHERE error IS NOT NULL AND error != '' ORDER BY created_at DESC"
        ).fetchall()

    def _normalize(e: str) -> str:
        return e.split(":", 1)[0].strip().lower()

    query_norm = _normalize(error) if error else ""

    out: list[dict[str, Any]] = []
    for row in rows:
        if len(out) >= limit:
            break

        row_error = row[6]
        if query_norm:
            row_norm = _normalize(row_error)
            if not (row_norm.startswith(query_norm) or query_norm.startswith(row_norm) or row_norm == query_norm):
                continue

        metrics: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        if row[7]:
            try:
                metrics = json.loads(row[7])
            except json.JSONDecodeError:
                metrics = {}
        if row[8]:
            try:
                extra = json.loads(row[8])
            except json.JSONDecodeError:
                extra = {}
        out.append(
            {
                "id": row[0],
                "action": row[1],
                "parent_id": row[2],
                "job_id": row[3],
                "cites": row[4],
                "summary": row[5],
                "error": row[6],
                "metrics": metrics,
                "extra": extra,
                "created_at": row[9],
                "hypothesis": row[10],
            }
        )
    return out


def _slim_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not metrics:
        return {}
    keep = (
        "ok",
        "ic",
        "ic_mean",
        "sharpe_ratio",
        "max_drawdown",
        "rows",
        "status",
        "factor_count",
        "via",
        "skipped",
        "reason",
        "portfolio_return",
        "verdict",
        "n_checks",
    )
    out: dict[str, Any] = {}
    for key in keep:
        if key in metrics and metrics[key] is not None:
            val = metrics[key]
            if key == "daily_returns":
                continue
            out[key] = val
    if "daily_returns" in metrics and isinstance(metrics["daily_returns"], dict):
        out["return_points"] = len(metrics["daily_returns"])
    return out


def _default_summary(action: str, metrics: dict[str, Any], error: str | None) -> str:
    if error:
        return f"{action} error={error[:160]}"
    bits = [action]
    for key in ("status", "ic_mean", "ok", "reason"):
        if key in metrics:
            bits.append(f"{key}={metrics[key]}")
    return " ".join(bits)


def record_job_finished(job: dict[str, Any]) -> dict[str, Any] | None:
    try:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        metrics = dict(result or {})
        if job.get("status"):
            metrics.setdefault("status", job.get("status"))
        return append_event(
            str(job.get("kind") or "job"),
            job_id=str(job.get("id") or "") or None,
            metrics=metrics,
            error=job.get("error"),
            summary=f"{job.get('kind')} {job.get('status')}",
        )
    except Exception:
        return None

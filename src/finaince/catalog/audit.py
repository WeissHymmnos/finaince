"""Append-only audit rows. Secrets never land in detail."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

_REDACT = {"api_key", "password", "token", "authorization", "secret", "llm_api_key"}


def _redact(detail: Any) -> Any:
    if isinstance(detail, dict):
        out = {}
        for key, value in detail.items():
            if str(key).lower() in _REDACT:
                out[key] = "***"
            else:
                out[key] = _redact(value)
        return out
    if isinstance(detail, list):
        return [_redact(item) for item in detail]
    return detail


def _connect():
    from finaince.catalog.store import FactorCatalog
    from finaince.db import ensure_columns

    cat = FactorCatalog()
    conn = sqlite3.connect(cat.db_path)
    ensure_columns(
        conn,
        "audit_log",
        [
            ("actor", "TEXT"),
            ("prev_hash", "TEXT"),
            ("hash", "TEXT"),
        ],
    )
    return conn


def append(action: str, detail: dict[str, Any] | None = None, actor: str = "cli") -> str:
    payload = _redact(detail or {})
    now = datetime.now(UTC).isoformat()
    with _connect() as conn:
        prev = conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = (prev[0] if prev and prev[0] else "") or ""
        blob = json.dumps(
            {"ts": now, "action": action, "detail": payload, "actor": actor, "prev": prev_hash},
            default=str,
            sort_keys=True,
        )
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO audit_log (ts, action, detail, actor, prev_hash, hash) VALUES (?,?,?,?,?,?)",
            (now, action, json.dumps(payload, default=str), actor, prev_hash, digest),
        )
        conn.commit()
    return digest


def list_audit(action: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        sql = "SELECT ts, action, detail, actor, prev_hash, hash FROM audit_log"
        args: list[Any] = []
        if action:
            sql += " WHERE action=?"
            args.append(action)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(sql, args).fetchall()
    out = []
    for row in rows:
        try:
            detail = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            detail = row[2]
        out.append(
            {
                "ts": row[0],
                "action": row[1],
                "detail": detail,
                "actor": row[3],
                "prev_hash": row[4],
                "hash": row[5],
            }
        )
    return out


def verify_tail(limit: int = 100) -> dict[str, Any]:
    rows = list(reversed(list_audit(limit=limit)))
    ok = True
    prev = ""
    for row in rows:
        if (row.get("prev_hash") or "") != prev and prev:
            # first row may have empty prev
            if row.get("prev_hash") not in {None, "", prev}:
                ok = False
                break
        prev = row.get("hash") or ""
    return {"ok": ok, "count": len(rows)}

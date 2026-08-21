"""SQLite catalog index (not a replacement SoR)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finaince.db import ensure_columns
from finaince.domain.factor import FactorRecord
from finaince.settings import get_settings

_DDL = """
CREATE TABLE IF NOT EXISTS factor_catalog (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    name TEXT,
    name_cn TEXT,
    status TEXT,
    dialect TEXT,
    formula TEXT,
    record_json TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(source, source_ref)
);
CREATE TABLE IF NOT EXISTS promotion_events (
    id TEXT PRIMARY KEY,
    catalog_id TEXT,
    direction TEXT,
    decision TEXT,
    gate_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    action TEXT,
    detail TEXT
);
"""


class FactorCatalog:
    def __init__(self, db_path: Path | None = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.platform_db)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_DDL)
            ensure_columns(conn, "factor_catalog", [("expr_hash", "TEXT")])

    def upsert(self, record: FactorRecord) -> FactorRecord:
        now = datetime.now(UTC).isoformat()
        record.updated_at = datetime.now(UTC)
        payload = record.model_dump(mode="json")
        expr_hash = self._hash_expression(record)
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, record_json FROM factor_catalog WHERE source=? AND source_ref=?",
                (record.lineage.source, record.lineage.source_ref),
            ).fetchone()
            if existing:
                record.id = existing[0]
                prev = json.loads(existing[1])
                payload["id"] = existing[0]
                payload["created_at"] = prev.get("created_at", payload["created_at"])
            conn.execute(
                """
                INSERT INTO factor_catalog
                    (id, source, source_ref, name, name_cn, status, dialect, formula, record_json, created_at, updated_at, expr_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_ref) DO UPDATE SET
                    name=excluded.name,
                    name_cn=excluded.name_cn,
                    status=excluded.status,
                    dialect=excluded.dialect,
                    formula=excluded.formula,
                    record_json=excluded.record_json,
                    updated_at=excluded.updated_at,
                    expr_hash=excluded.expr_hash
                """,
                (
                    record.id,
                    record.lineage.source,
                    record.lineage.source_ref,
                    record.name,
                    record.name_cn,
                    record.status,
                    record.expression.dialect,
                    record.expression.text,
                    json.dumps(payload, default=str),
                    payload.get("created_at", now),
                    now,
                    expr_hash,
                ),
            )
            conn.commit()
        try:
            from finaince.obs import emit

            emit("catalog_upsert", source=record.lineage.source)
        except Exception:
            pass
        return record

    @staticmethod
    def _hash_expression(record: FactorRecord) -> str:
        try:
            from finaince.expr_ast import expr_hash

            return expr_hash(record.expression.text, record.expression.dialect)
        except Exception:
            return ""

    def find_by_expr_hash(self, expression: str, dialect: str) -> list[dict[str, Any]]:
        """O(1)-indexed lookup of rows sharing the coarse-normalized tree hash."""
        try:
            from finaince.expr_ast import expr_hash

            digest = expr_hash(expression, dialect)
        except Exception:
            return []
        if not digest:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_catalog_expr_hash ON factor_catalog(expr_hash)")
            rows = conn.execute(
                "SELECT id, source, status, formula FROM factor_catalog WHERE expr_hash=?",
                (digest,),
            ).fetchall()
        return [{"id": r[0], "source": r[1], "status": r[2], "formula": r[3]} for r in rows]

    def get(self, catalog_id: str) -> FactorRecord | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT record_json FROM factor_catalog WHERE id=?",
                (catalog_id,),
            ).fetchone()
        if not row:
            return None
        return FactorRecord.model_validate(json.loads(row[0]))

    def list(
        self,
        source: str | None = None,
        status: str | None = None,
        query: str = "",
        style: str | None = None,
    ) -> list[FactorRecord]:
        sql = "SELECT record_json FROM factor_catalog WHERE 1=1"
        args: list[Any] = []
        if source:
            sql += " AND source=?"
            args.append(source)
        if status:
            sql += " AND status=?"
            args.append(status)
        if query:
            sql += " AND (name LIKE ? OR formula LIKE ? OR name_cn LIKE ?)"
            like = f"%{query}%"
            args.extend([like, like, like])
        sql += " ORDER BY updated_at DESC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, args).fetchall()
        records = [FactorRecord.model_validate(json.loads(r[0])) for r in rows]
        if style:
            records = [r for r in records if (r.style or "") == style]
        return records

    def set_status(self, catalog_id: str, status: str) -> FactorRecord | None:
        rec = self.get(catalog_id)
        if rec is None:
            return None
        rec.status = status  # type: ignore[assignment]
        return self.upsert(rec)

    def add_promotion(self, catalog_id: str, direction: str, decision: str, gates: dict) -> str:
        import uuid

        pid = uuid.uuid4().hex
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO promotion_events (id, catalog_id, direction, decision, gate_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, catalog_id, direction, decision, json.dumps(gates, default=str), datetime.now(UTC).isoformat()),
            )
            conn.commit()
        return pid

    def list_promotions(self, decision: str | None = "pending") -> list[dict[str, Any]]:
        sql = "SELECT id, catalog_id, direction, decision, gate_json, created_at FROM promotion_events"
        args: list[Any] = []
        if decision:
            sql += " WHERE decision=?"
            args.append(decision)
        sql += " ORDER BY created_at"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "catalog_id": row[1],
                    "direction": row[2],
                    "decision": row[3],
                    "gates": json.loads(row[4] or "{}"),
                    "created_at": row[5],
                }
            )
        return out

    def update_promotion(self, promo_id: str, decision: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE promotion_events SET decision=? WHERE id=?",
                (decision, promo_id),
            )
            conn.commit()


def default_catalog() -> FactorCatalog:
    return FactorCatalog()

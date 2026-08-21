import sqlite3


def ensure_columns(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]
) -> None:
    """Add missing columns idempotently and persist immediately.

    Every platform-DB migration must commit here so schema changes survive
    even when a caller crashes before its own transaction ends.
    """
    existing = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    changed = False
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            changed = True
    if changed:
        conn.commit()

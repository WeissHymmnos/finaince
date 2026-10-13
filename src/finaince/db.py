import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

_INITIALIZED_SCHEMAS: set[tuple[str, int]] = set()
_INIT_LOCK = threading.Lock()


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


@contextmanager
def managed_connection(
    db_path: str | Path,
    timeout: float = 30.0,
    row_factory: Any | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with WAL, busy_timeout, normal sync, foreign keys, and guaranteed close."""
    is_memory = str(db_path) == ":memory:"
    path = Path(db_path) if not is_memory else db_path
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_database_initialized(
    db_path: str | Path,
    ddl_statements: list[str] | str,
    migrations: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    """Execute DDL statements and indexes once per process for a database file."""
    is_memory = str(db_path) == ":memory:"
    resolved = ":memory:" if is_memory else str(Path(db_path).resolve())
    ddl_key = (
        hash(ddl_statements)
        if isinstance(ddl_statements, str)
        else hash(tuple(ddl_statements))
    )
    cache_key = (resolved, ddl_key)

    if cache_key in _INITIALIZED_SCHEMAS and not is_memory:
        return
    with _INIT_LOCK:
        if cache_key in _INITIALIZED_SCHEMAS and not is_memory:
            return
        path = Path(db_path) if not is_memory else db_path
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")

            if isinstance(ddl_statements, str):
                raw_stmts = [s.strip() for s in ddl_statements.split(";") if s.strip()]
            else:
                raw_stmts = []
                for s in ddl_statements:
                    raw_stmts.extend([part.strip() for part in s.split(";") if part.strip()])

            # 1. Execute table creation statements
            for stmt in raw_stmts:
                if stmt.upper().startswith("CREATE TABLE"):
                    conn.execute(stmt)

            # 2. Run migrations (which may add missing columns to existing tables)
            if migrations is not None:
                migrations(conn)

            # 3. Execute all other statements (e.g. indexes, triggers)
            for stmt in raw_stmts:
                if not stmt.upper().startswith("CREATE TABLE"):
                    conn.execute(stmt)

            conn.commit()
        finally:
            conn.close()
        if not is_memory:
            _INITIALIZED_SCHEMAS.add(cache_key)


def reset_db_initialization_cache() -> None:
    """Reset the database initialization cache (useful in tests)."""
    with _INIT_LOCK:
        _INITIALIZED_SCHEMAS.clear()

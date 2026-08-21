from __future__ import annotations

import sqlite3
from pathlib import Path

from finaince.trace import append_event, list_chain, recent_failures


def test_append_and_list_hypothesis(isolated_home: Path) -> None:
    ev1 = append_event("test_action", hypothesis="test hypothesis")
    assert ev1["hypothesis"] == "test hypothesis"

    ev2 = append_event("test_action_2")
    assert ev2["hypothesis"] is None

    chain = list_chain()
    assert len(chain) == 2
    assert chain[0]["hypothesis"] is None
    assert chain[1]["hypothesis"] == "test hypothesis"

def test_old_db_compatibility(isolated_home: Path) -> None:
    from finaince.catalog.store import FactorCatalog
    db_path = FactorCatalog().db_path

    # Create old table without hypothesis
    old_ddl = """
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
    with sqlite3.connect(db_path) as conn:
        conn.executescript(old_ddl)
        conn.execute(
            "INSERT INTO trace_events (id, action, created_at) VALUES (?, ?, ?)",
            ("old_id", "old_action", "2024-01-01T00:00:00")
        )
        conn.commit()

    # Now call list_chain, which should trigger ALTER TABLE
    chain = list_chain()
    assert len(chain) == 1
    assert chain[0]["id"] == "old_id"
    assert chain[0]["hypothesis"] is None

    # And append_event should work
    ev = append_event("new_action", hypothesis="new hyp")
    assert ev["hypothesis"] == "new hyp"

    chain2 = list_chain()
    assert len(chain2) == 2
    assert chain2[0]["hypothesis"] == "new hyp"

def test_recent_failures(isolated_home: Path) -> None:
    append_event("ok_action")
    append_event("fail_1", error="ValueError: bad")
    append_event("fail_2", error="ImportError: numpy missing at line 3")
    append_event("fail_3", error="ImportError: pandas missing")

    # Empty error string query returns all failures
    all_fails = recent_failures("")
    assert len(all_fails) == 3
    assert all_fails[0]["action"] == "fail_3"
    assert all_fails[1]["action"] == "fail_2"
    assert all_fails[2]["action"] == "fail_1"

    # None query returns all failures
    all_fails2 = recent_failures()
    assert len(all_fails2) == 3

    # Limit works
    limited = recent_failures(limit=2)
    assert len(limited) == 2
    assert limited[0]["action"] == "fail_3"
    assert limited[1]["action"] == "fail_2"

    # Match by normalized prefix
    matches = recent_failures("ImportError: numpy missing")
    assert len(matches) == 2
    # Both fail_2 and fail_3 have normalized error "importerror"
    # The query "ImportError: numpy missing" normalizes to "importerror"
    # So it matches both!
    assert matches[0]["action"] == "fail_3"
    assert matches[1]["action"] == "fail_2"

    # Does not match ValueError
    assert not any(m["action"] == "fail_1" for m in matches)

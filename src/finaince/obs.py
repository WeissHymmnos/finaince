"""Best-effort JSONL metrics. Failures are swallowed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def emit(event: str, **fields: Any) -> None:
    try:
        from finaince.settings import get_settings

        path = get_settings().home / "logs" / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    except Exception:
        return


def jobs_degraded(limit: int = 3) -> bool:
    """True when the most recent ``limit`` jobs all ended in error."""
    try:
        from finaince.jobs.runner import list_jobs

        rows = list_jobs()[: int(limit)]
        return len(rows) >= int(limit) and all(r.get("status") == "error" for r in rows)
    except Exception:
        return False

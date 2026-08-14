"""Optional catalog memory embed. Join is lineage.report_id only."""

from __future__ import annotations

from typing import Any

from finaince.domain.factor import FactorRecord


def empty_ok() -> dict[str, Any]:
    return {
        "ok": True,
        "knowledge_count": 0,
        "archetype_ids": [],
        "feedback_count": 0,
        "latest_failure_types": [],
    }


def memory_summary(record: FactorRecord) -> dict[str, Any]:
    key = record.lineage.report_id
    if not key:
        return empty_ok()
    try:
        from reproagent.memory.store import MemoryStore
        from reproagent.models.memory import FeedbackQuery
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"memory unavailable: {exc}"}
    try:
        store = MemoryStore()
        atoms = store.list_knowledge(report_id=key)
        raw = store.query_feedback(FeedbackQuery(include_mock=False, limit=10_000))
        feedback = [r for r in raw if getattr(r, "report_id", None) == key]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    types: list[str] = []
    for item in feedback:
        ft = getattr(item, "failure_type", None)
        if ft and str(ft) not in types:
            types.append(str(ft))
    archetypes = []
    for atom in atoms:
        aid = getattr(atom, "archetype_id", None)
        if aid and aid not in archetypes:
            archetypes.append(aid)
    return {
        "ok": True,
        "knowledge_count": len(atoms),
        "archetype_ids": archetypes,
        "feedback_count": len(feedback),
        "latest_failure_types": types[:8],
    }

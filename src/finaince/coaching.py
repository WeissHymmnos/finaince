"""Research-coaching layer: diverse samples and failure lessons."""

from __future__ import annotations

from typing import Any

import numpy as np

from finaince.catalog.store import FactorCatalog
from finaince.trace import recent_failures


def diverse_expression_samples(*, limit: int = 5) -> list[dict[str, Any]]:
    """Pull diverse factor samples using greedy CSS-style selection."""
    catalog = FactorCatalog()
    records = catalog.list()
    
    valid_records = []
    for r in records:
        if r.status in ("deprecated", "culled"):
            continue
        if not r.daily_returns:
            continue
        valid_records.append(r)
        
    if len(valid_records) < 2:
        return []
        
    valid_records.sort(key=lambda r: (-(abs(r.metrics.ic) if r.metrics.ic is not None else -float('inf')), r.id))
    
    selected = [valid_records[0]]
    remaining = valid_records[1:]
    
    while len(selected) < limit and remaining:
        best_idx = -1
        min_max_corr = float('inf')
        
        for i, cand in enumerate(remaining):
            max_corr = -float('inf')
            for sel in selected:
                dates = sorted(set(cand.daily_returns.keys()) & set(sel.daily_returns.keys()))
                if len(dates) < 10:
                    corr = 1.0
                else:
                    x = np.array([cand.daily_returns[d] for d in dates])
                    y = np.array([sel.daily_returns[d] for d in dates])
                    if np.std(x) == 0 or np.std(y) == 0:
                        corr = 1.0
                    else:
                        corr = abs(np.corrcoef(x, y)[0, 1])
                if corr > max_corr:
                    max_corr = corr
            
            if max_corr < min_max_corr:
                min_max_corr = max_corr
                best_idx = i
                
        selected.append(remaining.pop(best_idx))
        
    return [
        {
            "id": r.id,
            "name": r.name,
            "dialect": r.expression.dialect,
            "expression": r.expression.text,
            "ic": r.metrics.ic,
            "n_return_points": len(r.daily_returns),
        }
        for r in selected
    ]


def failure_lessons(*, error_prefix: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Wrap trace.recent_failures into lesson dicts."""
    failures = recent_failures(error=error_prefix, limit=limit)
    lessons = []
    for f in failures:
        error_head = (f.get("error") or "").split(":", 1)[0].strip()
        summary = f.get("summary") or ""
        if len(summary) > 120:
            summary = summary[:117] + "..."
        lessons.append({
            "id": f.get("id"),
            "error_head": error_head,
            "summary_short": summary,
            "hypothesis": f.get("hypothesis"),
        })
    return lessons


def research_context(
    *, error_prefix: str | None = None, sample_limit: int = 5, lesson_limit: int = 5
) -> dict[str, Any]:
    """Compose an evidence-backed context block."""
    try:
        samples = diverse_expression_samples(limit=sample_limit)
        lessons = failure_lessons(error_prefix=error_prefix, limit=lesson_limit)
        try:
            from finaince.process_memory import chains_display

            chains = chains_display(limit=3)
        except Exception:
            chains = []
        return {
            "ok": True,
            "samples": samples,
            "lessons": lessons,
            "chains": chains,
            "counts": {"samples": len(samples), "lessons": len(lessons), "chains": len(chains)},
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

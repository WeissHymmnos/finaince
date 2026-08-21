"""Shared MCP/SDK handlers. FastMCP signatures stay valid."""

from __future__ import annotations

from typing import Any

from finaince.discovery import cull_factor_pool, score_factor
from finaince.reproduction import search_library, validate_expression


def handle_cull_factor_pool(factors: list[dict[str, Any]]) -> dict[str, Any]:
    kept = cull_factor_pool(list(factors))
    kept_ids = {item.get("id") for item in kept}
    decisions = []
    for item in factors:
        keep = item.get("id") in kept_ids or item.get("hypothesis") in {
            k.get("hypothesis") for k in kept
        }
        decisions.append(
            {
                "hypothesis": item.get("hypothesis"),
                "decision": "keep" if keep else "cull",
                "perf_metric": item.get("perf_metric"),
            }
        )
    return {"kept_count": len(kept), "input_count": len(factors), "kept": kept, "decisions": decisions}


def handle_score_factor(**kwargs: Any) -> dict[str, Any]:
    """Dispatch by kwargs. Never silently swap backtest-grade for metrics-only."""
    has_metrics = "metrics" in kwargs
    has_expr = bool(kwargs.get("expression") or kwargs.get("backtest_id"))
    if has_metrics and has_expr:
        return {"error": "conflicting score_factor arguments", "status": 400}
    if has_expr:
        return run_library_grade_backtest(
            kwargs.get("expression"),
            backtest_id=kwargs.get("backtest_id"),
        )
    metrics = dict(kwargs.get("metrics") or {})
    return {
        "scorer": "selection_score",
        "score": score_factor(metrics, factor_ic=float(kwargs.get("factor_ic") or 0.0)),
        "metrics": metrics,
    }


def run_library_grade_backtest(expression: str | None, backtest_id: str | None = None) -> dict[str, Any]:
    from reproagent.mcp_server import library_grade_impl

    out = library_grade_impl(expression, backtest_id)
    if isinstance(out, dict):
        out = dict(out)
        out["scorer"] = "library_grade"
    return out


def handle_validate_expression(expression: str) -> dict[str, Any]:
    payload = validate_expression(expression)
    payload["expression"] = expression
    return payload


def handle_search_library(query: str = "", style: str | None = None, settings: Any = None) -> dict[str, Any]:
    from finaince.catalog.store import FactorCatalog

    records = FactorCatalog().list(query=query, style=style)
    items: list[dict[str, Any]] = []
    for rec in records:
        items.append(
            {
                "id": rec.id,
                "catalog_id": rec.id,
                "name": rec.name,
                "name_cn": rec.name_cn,
                "style": rec.style,
                "status": rec.status,
                "source": rec.lineage.source,
                "ic": rec.metrics.ic,
                "formula_proxy": rec.lineage.formula_proxy,
                "synthetic": "synthetic" in rec.tags,
            }
        )
    if not items:
        items = search_library(query=query, style=style, settings=settings)
    return {"count": len(items), "query": query, "style": style, "items": items}


def handle_reproduce_report(pdf_path: str, settings: Any = None) -> dict[str, Any]:
    from pathlib import Path

    from finaince.reproduction import reproduce_report

    result = reproduce_report(Path(pdf_path), settings=settings)
    return dict(result or {"status": "empty"})


def handle_catalog_list(query: str = "", source: str | None = None) -> dict[str, Any]:
    from finaince.catalog.store import FactorCatalog

    items = FactorCatalog().list(source=source, query=query)
    return {
        "count": len(items),
        "query": query,
        "source": source,
        "items": [r.model_dump(mode="json") for r in items],
    }


def handle_eval_expression(
    expression: str,
    dialect: str = "repro_polars",
    data_backend: str = "local",
) -> dict[str, Any]:
    from finaince.eval.router import EvalRequest, evaluate

    result = evaluate(
        EvalRequest(expression=expression, dialect=dialect, data_backend=data_backend)
    )
    return {
        "ok": result.ok,
        "dialect": result.dialect,
        "metrics": result.metrics,
        "error": result.error,
        "translatable": result.translatable,
        "alt_text": result.alt_text,
        "warnings": result.warnings,
    }


def handle_promote(catalog_id: str, direction: str = "to_pool") -> dict[str, Any]:
    from finaince.review.desk import promote

    return promote(catalog_id, direction=direction)


def handle_review_approve(promotion_id: str) -> dict[str, Any]:
    from finaince.review.desk import approve

    return approve(promotion_id)


def handle_review_reject(promotion_id: str) -> dict[str, Any]:
    from finaince.review.desk import reject

    return reject(promotion_id)


def handle_list_jobs() -> dict[str, Any]:
    from finaince.jobs.runner import list_jobs

    rows = list_jobs()
    return {"count": len(rows), "items": rows}


def handle_doctor() -> dict[str, Any]:
    from finaince.settings import doctor_report

    return doctor_report()


def handle_discover_swarm(args: list[str] | None = None, *, sync: bool = True) -> dict[str, Any]:
    from finaince.jobs.runner import run_swarm_job

    return run_swarm_job(list(args or []), sync=sync)


def handle_recent_failures(error: str | None = None, limit: int = 5) -> dict[str, Any]:
    from finaince.trace import recent_failures

    try:
        limit = max(1, min(50, int(limit)))
        if error == "":
            error = None
        items = recent_failures(error=error, limit=limit)
        return {"ok": True, "items": items, "count": len(items)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

def handle_research_context(error_prefix: str | None = None, sample_limit: int = 5, lesson_limit: int = 5) -> dict[str, Any]:
    from finaince.coaching import research_context
    
    try:
        sample_limit = max(1, min(20, int(sample_limit)))
        lesson_limit = max(1, min(20, int(lesson_limit)))
        if error_prefix == "":
            error_prefix = None
        return research_context(error_prefix=error_prefix, sample_limit=sample_limit, lesson_limit=lesson_limit)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

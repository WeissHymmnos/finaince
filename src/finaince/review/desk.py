"""Promotion desk: promote = pending; approve writes the other SoR."""

from __future__ import annotations

from typing import Any

from finaince.catalog.store import FactorCatalog
from finaince.domain.adapters import to_aiminer_dict
from finaince.review.gates import evaluate_gates
from finaince.settings import get_settings


def promote(catalog_id: str, *, direction: str, yes: bool = False) -> dict[str, Any]:
    from finaince.catalog.audit import append as audit_append

    cat = FactorCatalog()
    rec = cat.get(catalog_id)
    if rec is None:
        return {"ok": False, "error": f"unknown catalog id {catalog_id}"}
    gates = evaluate_gates(rec, direction=direction)
    rec.status = "review"
    cat.upsert(rec)
    pid = cat.add_promotion(catalog_id, direction, "pending", gates)
    audit_append("promote", {"catalog_id": catalog_id, "promotion_id": pid, "direction": direction})
    try:
        from finaince.obs import emit

        emit("promote_decision", direction=direction, passed=bool(gates.get("passed")), status="pending")
    except Exception:
        pass
    return {"ok": True, "promotion_id": pid, "status": "review", "gates": gates, "confirmed": yes}


def approve(promotion_id: str, *, override: list[str] | None = None) -> dict[str, Any]:
    from finaince.catalog.audit import append as audit_append

    cat = FactorCatalog()
    pending = [p for p in cat.list_promotions("pending") if p["id"] == promotion_id]
    if not pending:
        return {"ok": False, "error": "promotion not pending"}
    event = pending[0]
    rec = cat.get(event["catalog_id"])
    if rec is None:
        return {"ok": False, "error": "catalog row missing"}
    gates = evaluate_gates(rec, direction=event["direction"], override=override)
    if not gates["passed"]:
        try:
            from finaince.obs import emit

            emit("promote_decision", direction=event["direction"], passed=False, status="gates_failed")
        except Exception:
            pass
        return {"ok": False, "error": "gates_failed", "gates": gates}
    if event["direction"] == "to_pool":
        if not rec.daily_returns:
            return {"ok": False, "error": "empty_returns", "gates": gates}
        from aiminer.pool_io import persist_alpha_pool_rows
        from finaince.domain.adapters import mapped_aiminer_code

        mapped = to_aiminer_dict(rec)
        if not mapped.get("code") or not mapped_aiminer_code(rec):
            return {"ok": False, "error": "empty_code", "gates": gates}
        settings = get_settings()
        persist_alpha_pool_rows(
            settings.aiminer_db,
            settings.aiminer_results,
            [mapped],
        )
    elif event["direction"] == "to_library":
        _write_library(rec)
    rec.status = "ready"
    cat.upsert(rec)
    cat.update_promotion(promotion_id, "approved")
    gates["override"] = {"thin_panel": "thin_panel" in (override or [])}
    audit_append("approve", {"promotion_id": promotion_id, "catalog_id": rec.id})
    try:
        from finaince.obs import emit

        emit("promote_decision", direction=event["direction"], passed=True, status="approved")
    except Exception:
        pass
    return {"ok": True, "status": "ready", "gates": gates}


def reject(promotion_id: str) -> dict[str, Any]:
    from finaince.catalog.audit import append as audit_append

    cat = FactorCatalog()
    pending = [p for p in cat.list_promotions(None) if p["id"] == promotion_id]
    rec = None
    if pending:
        rec = cat.get(pending[0]["catalog_id"])
        if rec is not None:
            rec.status = "candidate"
            cat.upsert(rec)
    cat.update_promotion(promotion_id, "rejected")
    audit_append("reject", {"promotion_id": promotion_id, "catalog_id": rec.id if rec else None})
    try:
        from finaince.obs import emit

        emit("promote_decision", direction="reject", passed=False, status="rejected")
    except Exception:
        pass
    return {"ok": True, "status": "rejected"}


def _write_library(rec) -> None:
    from datetime import UTC, datetime
    from pathlib import Path
    from uuid import uuid4

    from reproagent.models.factor_def import FactorDefinition
    from reproagent.models.library import FactorLibraryEntry
    from reproagent.models.report import ResearchReport
    from reproagent.library.manager import FactorLibraryManager
    from reproagent.library.versioning import compute_dedup_hash
    from reproagent.persistence.db import get_engine, init_db
    from reproagent.persistence.paths import AppPaths
    from reproagent.persistence.repository import Repository
    from reproagent.settings import Settings
    from finaince.settings import get_settings

    settings = get_settings()
    st = Settings(data_dir=settings.repro_data_dir)
    st.data_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine(st.db_path)
    init_db(engine)
    repo = Repository(engine)
    paths = AppPaths.from_settings(st)
    paths.ensure_layout()
    import hashlib

    source_ref = rec.lineage.source_ref or rec.id
    report_id = rec.lineage.report_id or f"disc_{source_ref.replace(':', '')}"
    md = paths.reports_dir / "synthetic" / f"{source_ref}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    body = f"# {rec.name}\n\n{rec.expression.text}\n"
    md.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(md.read_bytes()).hexdigest()
    report = ResearchReport(
        id=report_id,
        file_path=md,
        file_hash=digest,
        title=rec.name,
        broker="finaince-discovery",
        page_count=1,
        validation_status="synthetic",
        ingested_at=datetime.now(UTC),
    )
    repo.save_report(report)
    fdef = FactorDefinition(
        id=uuid4().hex,
        spec_id=rec.lineage.spec_id or rec.id,
        name=rec.name,
        name_cn=rec.name_cn or rec.name,
        style=rec.style if rec.style in {
            "value", "growth", "momentum", "quality", "size",
            "volatility", "liquidity", "macro", "technical", "other",
        } else "other",
        formula=rec.expression.text if rec.expression.dialect == "repro_polars" else (rec.expression.alt_text or rec.expression.text),
        input_fields=list(rec.expression.input_fields),
        universe=rec.universe,
        rebalance_frequency=rec.rebalance_frequency or "daily",
    )
    entry = FactorLibraryEntry(
        id=uuid4().hex,
        factor=fdef,
        report_id=report_id,
        config_id="discovery",
        backtest_result_id=rec.lineage.source_ref,
        deviation_passed=False,
        version="0.1.0",
        dedup_hash=compute_dedup_hash(fdef),
        created_at=datetime.now(UTC),
    )
    FactorLibraryManager(repository=repo, paths=paths).register(entry)
    if "synthetic" not in rec.tags:
        rec.tags.append("synthetic")
    if "source:discovery" not in rec.tags:
        rec.tags.append("source:discovery")
    FactorCatalog().upsert(rec)

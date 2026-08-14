"""Rebuild catalog from engine stores (SELECT only for rustminer)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from finaince.catalog.hooks import accept_library_entry, accept_pool_row
from finaince.catalog.store import FactorCatalog
from finaince.settings import get_settings


def rebuild(*, source: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    counts = {"discovery": 0, "reproduction": 0, "rustminer": 0}
    if source in (None, "discovery", "aiminer"):
        from aiminer.pool_io import load_alpha_pool_rows

        for row in load_alpha_pool_rows(settings.aiminer_db):
            accept_pool_row(row, engine_db="aiminer")
            counts["discovery"] += 1
    if source in (None, "rustminer"):
        rust_db = settings.rustminer_db or (settings.home / "rustminer" / "results" / "alpha_miner.db")
        env = __import__("os").environ.get("FINAINCE_RUSTMINER_DB")
        if env:
            rust_db = Path(env)
        if Path(rust_db).exists():
            from aiminer.pool_io import load_alpha_pool_rows

            for row in load_alpha_pool_rows(rust_db):
                accept_pool_row(row, engine_db="rustminer")
                counts["rustminer"] += 1
    if source in (None, "reproduction", "reproagent"):
        try:
            from reproagent.library.manager import FactorLibraryManager
            from reproagent.persistence.db import get_engine, init_db
            from reproagent.persistence.paths import AppPaths
            from reproagent.persistence.repository import Repository
            from reproagent.settings import Settings

            st = Settings(data_dir=settings.repro_data_dir)
            engine = get_engine(st.db_path)
            init_db(engine)
            repo = Repository(engine)
            mgr = FactorLibraryManager(repository=repo, paths=AppPaths.from_settings(st))
            from reproagent.reproducer.metrics import serialize_equity_returns

            for entry in mgr.list():
                extras: dict[str, Any] = {"metrics": {}, "daily_returns": {}, "observability": {}}
                bt_id = getattr(entry, "backtest_result_id", None)
                if bt_id:
                    eq = settings.repro_data_dir / "backtest" / str(bt_id) / "equity_curve.parquet"
                    if eq.is_file():
                        extras["daily_returns"] = serialize_equity_returns(eq)
                accept_library_entry(entry, extras=extras, allow_incomplete=True)
                counts["reproduction"] += 1
        except Exception:
            pass
    return {"ok": True, "counts": counts, "total": len(FactorCatalog().list())}


def retag_synthetic() -> dict[str, Any]:
    """Tag discovery/synthetic writes only. Real validated reports stay untouched."""
    cat = FactorCatalog()
    tagged = 0
    skipped = 0
    for rec in cat.list():
        is_disc = rec.lineage.source == "discovery" or "source:discovery" in rec.tags
        if not is_disc:
            skipped += 1
            continue
        changed = False
        if "synthetic" not in rec.tags:
            rec.tags.append("synthetic")
            changed = True
        if "source:discovery" not in rec.tags:
            rec.tags.append("source:discovery")
            changed = True
        if changed:
            cat.upsert(rec)
            tagged += 1
    reports = 0
    try:
        from sqlmodel import Session, select

        from reproagent.persistence.db import get_engine, init_db
        from reproagent.persistence.repository import Repository
        from reproagent.persistence.tables import ReportTable
        from reproagent.settings import Settings

        settings = get_settings()
        st = Settings(data_dir=settings.repro_data_dir)
        engine = get_engine(st.db_path)
        init_db(engine)
        repo = Repository(engine)
        with Session(engine) as session:
            rows = list(session.exec(select(ReportTable)))
        for row in rows:
            path = str(getattr(row, "file_path", "") or "").replace("\\", "/")
            broker = str(getattr(row, "broker", "") or "")
            marker = broker == "finaince-discovery" or "/reports/synthetic/" in path
            if not marker:
                continue
            if getattr(row, "validation_status", None) == "synthetic":
                continue
            report = repo.get_report(str(row.id))
            if report is None:
                continue
            report.validation_status = "synthetic"
            repo.save_report(report)
            reports += 1
    except Exception:
        pass
    return {
        "ok": True,
        "catalog_tagged": tagged,
        "catalog_skipped": skipped,
        "reports_retagged": reports,
    }

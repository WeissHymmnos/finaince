"""Reproduction façade: shipped reproagent pipeline, validate, library search."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from finaince._paths import ensure_import_paths

ensure_import_paths()


def reproduce_report(
    pdf_path: str | Path,
    settings: Any | None = None,
    backtest_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """End-to-end 研报复现 via the shipped ``reproagent.pipeline.reproduce_report``."""
    from reproagent.pipeline import reproduce_report as _reproduce_report
    from reproagent.settings import Settings

    if settings is None:
        from finaince.settings import reproagent_runtime_settings

        cfg = reproagent_runtime_settings()
    else:
        cfg = settings
    if not isinstance(cfg, Settings):
        raise TypeError(f"settings must be reproagent.settings.Settings, got {type(cfg)!r}")
    kwargs = dict(backtest_kwargs or {})
    if "start_date" not in kwargs or "end_date" not in kwargs:
        from finaince.runtime import default_backtest_window

        window = default_backtest_window(cfg.data_source)
        kwargs.setdefault("start_date", window["start_date"])
        kwargs.setdefault("end_date", window["end_date"])
    raw = _reproduce_report(Path(pdf_path), cfg, backtest_kwargs=kwargs)
    from finaince.impl_status import annotate_reproduce

    return annotate_reproduce(raw if isinstance(raw, dict) else {"status": "no_factors", "factors": []})


def validate_expression(expression: str) -> dict[str, Any]:
    """Static factor-expression check from the shipped polars engine."""
    from reproagent.reproducer.polars_engine import validate_expression as _validate

    return dict(_validate(expression))


def search_library(
    query: str = "",
    style: str | None = None,
    settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Search the shipped factor library (name / Chinese name / style)."""
    from reproagent.library.manager import FactorLibraryManager
    from reproagent.persistence.db import get_engine, init_db
    from reproagent.persistence.paths import AppPaths
    from reproagent.persistence.repository import Repository

    if settings is None:
        from finaince.settings import reproagent_runtime_settings

        settings = reproagent_runtime_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = get_engine(settings.db_path)
    init_db(engine)
    repo = Repository(engine)
    paths = AppPaths.from_settings(settings)
    paths.ensure_layout()
    manager = FactorLibraryManager(repository=repo, paths=paths)
    entries = manager.list()
    q = (query or "").lower()
    results: list[dict[str, Any]] = []
    for entry in entries:
        if style and entry.factor.style != style:
            continue
        name_l = entry.factor.name.lower()
        cn_l = (entry.factor.name_cn or "").lower()
        if q and q not in name_l and q not in cn_l:
            continue
        results.append(
            {
                "id": entry.id,
                "name": entry.factor.name,
                "name_cn": entry.factor.name_cn,
                "style": entry.factor.style,
                "status": entry.status,
            }
        )
    return results

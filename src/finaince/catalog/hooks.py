"""Lazy dual-write entry points used by engine persist/register sites."""

from __future__ import annotations

import os
from typing import Any

from finaince.domain.adapters import from_aiminer_dict, from_library_entry
from finaince.catalog.store import FactorCatalog


def _enabled() -> bool:
    return os.environ.get("FINAINCE_CATALOG", "1") != "0"


def accept_pool_row(factor: dict[str, Any], *, engine_db: str | None = None) -> None:
    if not _enabled():
        return
    if not (factor.get("returns") or factor.get("daily_returns")):
        return
    record = from_aiminer_dict(factor, engine_db=engine_db)
    FactorCatalog().upsert(record)


def accept_library_entry(
    entry: Any,
    *,
    extras: dict[str, Any],
    allow_incomplete: bool = False,
) -> None:
    if not _enabled():
        return
    if not extras or "metrics" not in extras:
        return
    if not allow_incomplete:
        metrics = extras.get("metrics") or {}
        returns = extras.get("daily_returns") or {}
        # Equity curves can be empty (0-row parquet) while IC still exists.
        # Index the row; promote gates still fail-closed on missing returns.
        if metrics.get("ic_mean") is None and not returns:
            return
    record = from_library_entry(entry, extras=extras)
    FactorCatalog().upsert(record)

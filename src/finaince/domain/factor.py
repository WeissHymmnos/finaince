"""Platform FactorRecord (index type; engines keep their own types)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def finite_ic(value: Any) -> float | None:
    """Return a finite IC, or None if missing / NaN / Inf."""
    if value is None:
        return None
    try:
        ic = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ic):
        return None
    return ic

Dialect = Literal["qlib", "repro_polars", "python_sandbox"]
Source = Literal["discovery", "reproduction", "manual"]
Status = Literal["candidate", "review", "ready", "deprecated", "culled"]


class FactorExpression(BaseModel):
    dialect: Dialect
    text: str
    input_fields: list[str] = Field(default_factory=list)
    validated: bool = False
    translatable: bool = False
    alt_text: str | None = None


class FactorMetrics(BaseModel):
    ic: float | None = None
    rank_ic: float | None = None
    sharpe: float | None = None
    annualized_return: float | None = None
    max_drawdown: float | None = None
    turnover: float | None = None
    cost_drag: float | None = None
    selection_score: float | None = None
    library_grade: str | None = None
    library_score: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class FactorLineage(BaseModel):
    source: Source
    source_ref: str
    run_id: str | None = None
    report_id: str | None = None
    spec_id: str | None = None
    parent_id: str | None = None
    engine_db: str | None = None
    formula_proxy: bool = False
    formula_fallback: bool = False
    universe_fallback: bool = False
    recovery_used: bool = False


class FactorRecord(BaseModel):
    id: str
    name: str
    name_cn: str | None = None
    hypothesis: str | None = None
    role: str | None = None
    style: str | None = None
    expression: FactorExpression
    universe: str = "local_panel"
    market_profile: str = "cn_stock"
    rebalance_frequency: str | None = None
    metrics: FactorMetrics = Field(default_factory=FactorMetrics)
    daily_returns: dict[str, float] = Field(default_factory=dict)
    status: Status = "candidate"
    lineage: FactorLineage
    tags: list[str] = Field(default_factory=list)
    is_simulated: bool = False
    created_at: datetime
    updated_at: datetime

"""Adapters between engine types and FactorRecord."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from finaince.domain.factor import (
    FactorExpression,
    FactorLineage,
    FactorMetrics,
    FactorRecord,
    finite_ic,
)


def _new_id() -> str:
    return f"fac_{uuid.uuid4().hex[:16]}"


def _serialize_returns(returns: Any) -> dict[str, float]:
    if returns is None:
        return {}
    if isinstance(returns, dict):
        out: dict[str, float] = {}
        for k, v in returns.items():
            try:
                key = k.isoformat() if hasattr(k, "isoformat") else str(k)
                out[key] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    return {}


def _fields_from_text(text: str) -> list[str]:
    return sorted(set(re.findall(r"\$?([A-Za-z_][A-Za-z0-9_]*)", text or "")))


def from_aiminer_dict(item: dict[str, Any], *, engine_db: str | None = None) -> FactorRecord:
    now = datetime.now(UTC)
    source_ref = str(item.get("id") or f"alpha_{uuid.uuid4().hex[:8]}")
    metrics_src = item.get("metrics") or {}
    ic = item.get("ic")
    if ic is None:
        ic = metrics_src.get("information_coefficient", item.get("perf_metric"))
    name = str(item.get("hypothesis") or item.get("role") or source_ref)
    tags = list(item.get("tags") or [])
    if engine_db:
        tags.append(f"engine:{engine_db}")
    return FactorRecord(
        id=_new_id(),
        name=name,
        name_cn=name,
        hypothesis=item.get("hypothesis"),
        role=item.get("role"),
        style="other",
        expression=FactorExpression(
            dialect="qlib",
            text=str(item.get("code") or ""),
            input_fields=_fields_from_text(str(item.get("code") or "")),
        ),
        universe=str(item.get("universe") or "local_panel"),
        market_profile=str(item.get("market_profile") or "cn_stock"),
        rebalance_frequency="daily",
        metrics=FactorMetrics(
            ic=finite_ic(ic),
            rank_ic=item.get("rank_ic") or metrics_src.get("rank_ic"),
            selection_score=item.get("selection_score"),
            extra=dict(metrics_src),
        ),
        daily_returns=_serialize_returns(item.get("returns")),
        status="candidate",
        lineage=FactorLineage(
            source="discovery",
            source_ref=source_ref,
            run_id=item.get("run_id"),
            engine_db=engine_db,
        ),
        tags=tags,
        is_simulated=bool(item.get("is_simulated") or (metrics_src.get("_simulated"))),
        created_at=now,
        updated_at=now,
    )


def mapped_aiminer_code(record: FactorRecord) -> str:
    """Expression text the alpha_pool row should store.

    qlib dialect uses ``text``. repro_polars prefers a translated ``alt_text``
    but must fall back to the original formula so cross-feed never writes "".
    """
    if record.expression.dialect == "qlib":
        return str(record.expression.text or record.expression.alt_text or "").strip()
    return str(record.expression.alt_text or record.expression.text or "").strip()


def to_aiminer_dict(record: FactorRecord) -> dict[str, Any]:
    ref = record.lineage.source_ref
    if ref.startswith("alpha_"):
        aid = ref
    else:
        aid = f"alpha_{record.id[-8:]}"
    code = mapped_aiminer_code(record)
    if not code:
        raise ValueError("to_aiminer_dict: empty code after dialect mapping")
    return {
        "id": aid,
        "role": record.role or "reproagent",
        "hypothesis": record.hypothesis or record.name,
        "code": code,
        "perf_metric": record.metrics.ic if record.metrics.ic is not None else 0.0,
        "selection_score": record.metrics.selection_score,
        "returns": dict(record.daily_returns),
        "market_profile": record.market_profile,
        "is_simulated": record.is_simulated,
        "metrics": {
            "information_coefficient": record.metrics.ic,
            "rank_ic": record.metrics.rank_ic,
        },
    }


def _scoped_proxy(obs: dict[str, Any], factor_name: str) -> bool:
    """Run-level formula_proxy taints every sibling. Prefer proxy_factors."""
    proxies = [str(x) for x in (obs.get("proxy_factors") or []) if str(x)]
    if proxies:
        return factor_name in proxies
    return bool(obs.get("formula_proxy"))


def from_library_entry(entry: Any, *, extras: dict[str, Any] | None = None) -> FactorRecord:
    extras = extras or {}
    now = datetime.now(UTC)
    factor = entry.factor
    metrics_src = extras.get("metrics") or {}
    obs = extras.get("observability") or {}
    ic = finite_ic(metrics_src.get("ic_mean"))
    factor_name = str(getattr(factor, "name", "") or "")
    try:
        from finaince.eval.dialects import attach_translation

        mapped = attach_translation(str(factor.formula or ""), "repro_polars")
    except Exception:
        mapped = {"translatable": False, "alt_text": None}
    universe = str(getattr(factor, "universe", "") or "local_panel")
    try:
        from finaince.review.gates import _claims_broad_universe
        from finaince.runtime import local_panel_is_thin

        if local_panel_is_thin() and _claims_broad_universe(universe):
            universe = "local_panel"
    except Exception:
        pass
    return FactorRecord(
        id=_new_id(),
        name=factor.name,
        name_cn=factor.name_cn,
        style=factor.style,
        expression=FactorExpression(
            dialect="repro_polars",
            text=factor.formula,
            input_fields=list(factor.input_fields or []),
            translatable=bool(mapped.get("translatable")),
            alt_text=mapped.get("alt_text") if isinstance(mapped.get("alt_text"), str) else None,
        ),
        universe=universe,
        rebalance_frequency=factor.rebalance_frequency,
        metrics=FactorMetrics(
            ic=ic,
            sharpe=metrics_src.get("sharpe_ratio"),
            max_drawdown=metrics_src.get("max_drawdown"),
            extra=dict(metrics_src),
        ),
        daily_returns=dict(extras.get("daily_returns") or {}),
        status="candidate",
        lineage=FactorLineage(
            source="reproduction",
            source_ref=entry.id,
            report_id=entry.report_id,
            spec_id=factor.spec_id,
            formula_proxy=_scoped_proxy(obs, factor_name),
            formula_fallback=bool(obs.get("formula_fallback")),
            universe_fallback=bool(obs.get("universe_fallback")),
            recovery_used=bool(obs.get("recovery_used")),
        ),
        created_at=now,
        updated_at=now,
    )

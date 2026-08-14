"""Fail-closed promotion gates."""

from __future__ import annotations

from typing import Any

from finaince.domain.factor import FactorRecord


_CSI300_CLAIMS = {
    "csi300",
    "hs300",
    "沪深300",
    "csi500",
    "zz500",
    "中证500",
    "csi1000",
    "中证1000",
    "全a股",
    "全a",
    "a股",
    "全市场",
    "all",
}


def _claims_broad_universe(universe: str) -> bool:
    key = (universe or "").strip().lower().replace(" ", "")
    return key in _CSI300_CLAIMS


def evaluate_gates(
    record: FactorRecord,
    *,
    direction: str,
    override: list[str] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    skipped = set(override or [])
    if record.is_simulated:
        failures.append("simulated")
    if record.lineage.formula_proxy:
        failures.append("formula_proxy")
    if "thin_panel" not in skipped and _claims_broad_universe(record.universe):
        try:
            from finaince.runtime import local_panel_is_thin

            if local_panel_is_thin():
                failures.append("thin_panel")
        except Exception:
            pass
    if record.metrics.ic is None:
        failures.append("missing_ic")
    else:
        if direction == "to_pool" and abs(float(record.metrics.ic)) <= 0.005:
            failures.append("ic_threshold")
    returns = record.daily_returns or {}
    if not returns:
        failures.append("missing_returns")
    else:
        try:
            from aiminer.manager import _series_correlation
            import pandas as pd

            series = pd.Series({k: float(v) for k, v in returns.items()})
            # Compare against existing pool if present.
            from finaince.settings import get_settings
            from aiminer.pool_io import load_alpha_pool_rows

            settings = get_settings()
            for row in load_alpha_pool_rows(settings.aiminer_db):
                other = row.get("returns") or {}
                if not other:
                    continue
                oseries = pd.Series({k: float(v) for k, v in other.items()})
                corr = _series_correlation(series, oseries)
                if corr is None:
                    failures.append("uncorrelatable_returns")
                    break
                if corr > 0.7:
                    failures.append("correlated")
                    break
        except Exception as exc:  # noqa: BLE001
            failures.append(f"corr_error:{exc}")
        if direction == "to_library" and "correlated" not in failures:
            try:
                from finaince.catalog.store import FactorCatalog

                for other in FactorCatalog().list(source="reproduction"):
                    if other.id == record.id or not other.daily_returns:
                        continue
                    oseries = pd.Series({k: float(v) for k, v in other.daily_returns.items()})
                    corr = _series_correlation(series, oseries)
                    if corr is not None and corr > 0.7:
                        failures.append("correlated")
                        break
            except Exception as exc:  # noqa: BLE001
                failures.append(f"corr_error:{exc}")
    if direction == "to_pool":
        from finaince.domain.adapters import mapped_aiminer_code

        if not mapped_aiminer_code(record):
            failures.append("empty_code")
    passed = not failures
    return {"passed": passed, "failures": failures, "direction": direction}

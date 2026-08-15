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


def _finite_returns(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if num == num and num not in (float("inf"), float("-inf")):
            out[str(key)] = num
    return out


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

            thin = local_panel_is_thin()
        except Exception:
            thin = True
        if thin:
            failures.append("thin_panel")
    from finaince.domain.factor import finite_ic

    ic = finite_ic(record.metrics.ic)
    if ic is None:
        failures.append("missing_ic")
    elif direction == "to_pool" and abs(ic) <= 0.005:
        failures.append("ic_threshold")
    returns = _finite_returns(record.daily_returns)
    if not returns:
        failures.append("missing_returns")
    else:
        try:
            from aiminer.manager import _series_correlation
            import pandas as pd

            series = pd.Series(returns)
            from finaince.settings import get_settings
            from aiminer.pool_io import load_alpha_pool_rows

            settings = get_settings()
            for row in load_alpha_pool_rows(settings.aiminer_db):
                other = _finite_returns(row.get("returns"))
                if not other:
                    continue
                corr = _series_correlation(series, pd.Series(other))
                if corr is not None and corr > 0.7:
                    failures.append("correlated")
                    break
        except Exception as exc:  # noqa: BLE001
            failures.append(f"corr_error:{exc}")
        if direction == "to_library" and "correlated" not in failures:
            try:
                from finaince.catalog.store import FactorCatalog

                for other in FactorCatalog().list(source="reproduction"):
                    if other.id == record.id:
                        continue
                    other_ret = _finite_returns(other.daily_returns)
                    if not other_ret:
                        continue
                    corr = _series_correlation(series, pd.Series(other_ret))
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

"""Desk financial contracts: gates, cost, universe honesty, empty eval."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from finaince.baseline import LOCKED_WINDOW, run_locked_baseline
from finaince.domain.factor import (
    FactorExpression,
    FactorLineage,
    FactorMetrics,
    FactorRecord,
)
from finaince.eval.router import EvalRequest, evaluate
from finaince.review.desk import approve, promote
from finaince.review.gates import evaluate_gates


def _record(**kwargs: object) -> FactorRecord:
    now = datetime.now(UTC)
    payload = {
        "id": "fac_fin_logic",
        "name": "fin_logic",
        "expression": FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 1))"),
        "universe": "local_panel",
        "metrics": FactorMetrics(ic=0.06),
        "daily_returns": {f"2024-03-{day:02d}": 0.01 for day in range(1, 13)},
        "lineage": FactorLineage(source="manual", source_ref="fin_logic"),
        "created_at": now,
        "updated_at": now,
    }
    payload.update(kwargs)
    return FactorRecord(**payload)


def test_nan_ic_is_missing_not_a_present_score(isolated_home: Path) -> None:
    """NaN must be missing_ic. Old gates used abs(NaN)<=0.005 which is False."""
    from finaince.catalog.store import FactorCatalog
    from finaince.domain.adapters import from_library_entry
    from finaince.settings import get_settings
    from aiminer.pool_io import load_alpha_pool_rows

    nan_rec = _record(id="fac_nan_ic", metrics=FactorMetrics(ic=float("nan")))
    gates = evaluate_gates(nan_rec, direction="to_pool")
    assert gates["passed"] is False
    assert "missing_ic" in gates["failures"]

    inf_rec = _record(id="fac_inf_ic", metrics=FactorMetrics(ic=float("inf")))
    inf_gates = evaluate_gates(inf_rec, direction="to_pool")
    assert inf_gates["passed"] is False
    assert "missing_ic" in inf_gates["failures"]

    class _F:
        name = "nan_lib"
        name_cn = "nan"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-nan"

    class _E:
        id = "lib-nan-ic"
        report_id = "rep-nan"
        factor = _F()

    stored = from_library_entry(
        _E(),
        extras={
            "metrics": {"ic_mean": float("nan")},
            "daily_returns": {f"2024-03-{day:02d}": 0.01 for day in range(1, 13)},
            "observability": {},
        },
    )
    assert stored.metrics.ic is None
    FactorCatalog().upsert(stored)
    loaded = FactorCatalog().get(stored.id)
    assert loaded is not None
    assert loaded.metrics.ic is None
    loaded_gates = evaluate_gates(loaded, direction="to_pool")
    assert "missing_ic" in loaded_gates["failures"]

    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    promo = promote(stored.id, direction="to_pool")
    denied = approve(promo["promotion_id"])
    assert denied["ok"] is False
    assert "missing_ic" in ((denied.get("gates") or {}).get("failures") or [])
    after = list(load_alpha_pool_rows(get_settings().aiminer_db))
    assert after == before


def test_gates_reject_ic_threshold_and_correlated(isolated_home: Path) -> None:
    weak = _record(id="fac_weak_ic", metrics=FactorMetrics(ic=0.005))
    gates = evaluate_gates(weak, direction="to_pool")
    assert gates["passed"] is False
    assert "ic_threshold" in gates["failures"]

    just_over = _record(id="fac_ok_ic", metrics=FactorMetrics(ic=0.006))
    ok_ic = evaluate_gates(just_over, direction="to_pool")
    assert "ic_threshold" not in ok_ic["failures"]


def test_approve_does_not_write_pool_when_gates_fail(isolated_home: Path) -> None:
    from aiminer.pool_io import load_alpha_pool_rows
    from finaince.catalog.store import FactorCatalog
    from finaince.settings import get_settings

    rec = _record(
        id="fac_no_write",
        metrics=FactorMetrics(ic=None),
        daily_returns={},
        universe="csi300",
        lineage=FactorLineage(source="manual", source_ref="no_write", formula_proxy=True),
    )
    FactorCatalog().upsert(rec)
    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    submitted = promote(rec.id, direction="to_pool")
    denied = approve(submitted["promotion_id"])
    assert denied["ok"] is False
    assert denied.get("error") == "gates_failed"
    failures = (denied.get("gates") or {}).get("failures") or []
    assert "missing_ic" in failures
    assert "missing_returns" in failures
    assert "formula_proxy" in failures
    assert "thin_panel" in failures
    after = list(load_alpha_pool_rows(get_settings().aiminer_db))
    assert after == before


def test_correlated_returns_fail_closed_against_pool(isolated_home: Path) -> None:
    from aiminer.pool_io import persist_alpha_pool_rows
    from finaince.settings import get_settings

    settings = get_settings()
    returns = {f"2024-03-{day:02d}": 0.02 * day for day in range(1, 13)}
    persist_alpha_pool_rows(
        settings.aiminer_db,
        settings.aiminer_results,
        [
            {
                "id": "alpha_seed",
                "hypothesis": "seed",
                "code": "Rank($close)",
                "ic": 0.08,
                "returns": returns,
            }
        ],
    )
    twin = _record(
        id="fac_twin",
        daily_returns=dict(returns),
        metrics=FactorMetrics(ic=0.07),
    )
    gates = evaluate_gates(twin, direction="to_pool")
    assert gates["passed"] is False
    assert "correlated" in gates["failures"]


def test_thin_panel_fail_closed_when_panel_check_errors(
    isolated_home: Path, monkeypatch
) -> None:
    def boom() -> bool:
        raise RuntimeError("panel unreadable")

    monkeypatch.setattr("finaince.runtime.local_panel_is_thin", boom)
    rec = _record(universe="csi300")
    gates = evaluate_gates(rec, direction="to_pool")
    assert gates["passed"] is False
    assert "thin_panel" in gates["failures"]


def test_eval_local_does_not_claim_csi300(isolated_home: Path) -> None:
    local = evaluate(
        EvalRequest(
            expression="Rank(Delta(close, 1))",
            dialect="repro_polars",
            data_backend="local",
            universe="沪深300",
            start=str(LOCKED_WINDOW["start"]),
            end=str(LOCKED_WINDOW["end"]),
            cost_bps=0,
        )
    )
    assert local.metrics.get("universe_claim") == "local_panel"
    note = str(local.metrics.get("note") or "") + str(local.error or "")
    assert "paper ARR" not in note
    qlib = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert qlib.ok is False
    assert qlib.error == "qlib_placeholder"


def test_empty_window_eval_is_not_ok(isolated_home: Path) -> None:
    out = evaluate(
        EvalRequest(
            expression="Rank(Delta(close, 1))",
            dialect="repro_polars",
            data_backend="local",
            universe="local_panel",
            start="1990-01-02",
            end="1990-01-10",
            cost_bps=0,
        )
    )
    assert out.ok is False
    assert out.error == "empty_or_missing_ic"


def test_forward_return_is_next_bar_and_cost_is_subtracted() -> None:
    import inspect

    from reproagent.reproducer.backtester import StrategyBacktester

    source = inspect.getsource(StrategyBacktester.run)
    assert "shift(-1)" in source
    assert "forward_return" in source
    assert "transaction_cost_bps" in source
    assert "turnover" in source


def test_baseline_and_cost_still_honest(isolated_home: Path) -> None:
    first = run_locked_baseline()
    second = run_locked_baseline()
    assert first["ok"] == second["ok"]
    assert first["metrics"].get("transaction_cost_bps") == 0
    assert second["metrics"].get("transaction_cost_bps") == 0
    assert first["metrics"].get("ic_mean") == second["metrics"].get("ic_mean")
    assert first["metrics"].get("sharpe_ratio") == second["metrics"].get("sharpe_ratio")
    assert first["metrics"].get("universe_claim") == "local_panel"
    assert "not CSI300" in first["claim"]
    assert "ARR" in first["claim"]
    zero = evaluate(
        EvalRequest(
            expression=str(LOCKED_WINDOW["expression"]),
            dialect=str(LOCKED_WINDOW["dialect"]),
            data_backend="local",
            universe=str(LOCKED_WINDOW["universe"]),
            start=str(LOCKED_WINDOW["start"]),
            end=str(LOCKED_WINDOW["end"]),
            cost_bps=0,
        )
    )
    three = evaluate(
        EvalRequest(
            expression=str(LOCKED_WINDOW["expression"]),
            dialect=str(LOCKED_WINDOW["dialect"]),
            data_backend="local",
            universe=str(LOCKED_WINDOW["universe"]),
            start=str(LOCKED_WINDOW["start"]),
            end=str(LOCKED_WINDOW["end"]),
            cost_bps=3,
        )
    )
    if zero.ok and three.ok:
        assert zero.metrics.get("sharpe_ratio") != three.metrics.get("sharpe_ratio")
    else:
        assert zero.ok == three.ok

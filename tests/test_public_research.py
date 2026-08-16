"""Public-research contract: stranger install, locked baseline, fail-closed gates."""

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
from finaince.review.gates import evaluate_gates

ROOT = Path(__file__).resolve().parents[1]


def _record(**kwargs: object) -> FactorRecord:
    now = datetime.now(UTC)
    payload = {
        "id": "fac_public_research",
        "name": "public_research",
        "expression": FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 1))"),
        "universe": "local_panel",
        "metrics": FactorMetrics(ic=0.06),
        "daily_returns": {f"2024-03-{day:02d}": 0.01 for day in range(1, 13)},
        "lineage": FactorLineage(source="manual", source_ref="public_research"),
        "created_at": now,
        "updated_at": now,
    }
    payload.update(kwargs)
    return FactorRecord(**payload)


def test_public_install_contract_has_no_private_token() -> None:
    workflow = (ROOT / ".github" / "workflows" / "packaging-312.yml").read_text()
    pytest_wf = (ROOT / ".github" / "workflows" / "pytest-offline.yml").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()
    readme = (ROOT / "README.md").read_text()
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    assert "SIBLING_CHECKOUT_TOKEN" not in workflow
    assert "SIBLING_CHECKOUT_TOKEN" not in pytest_wf
    assert "secrets." not in workflow
    assert "uv pip install -e \".[reproduction]\"" in workflow
    assert "FINAINCE_NO_PATH_HACK" in workflow
    assert "from aiminer.manager import cull_alpha_pool" in workflow
    assert "print('ok'" in workflow
    assert "18702585ed19f50c85c9dd9b023ebff2a09a56f7" in pyproject
    assert "99e0924e41cc6c8d70d7670cdf1cf0c63bb3aeaa" in pyproject
    assert "pyarrow" in pyproject
    assert "--override thin_panel" in readme
    assert "ok: false" not in readme
    assert "FINAINCE_DESK_TOKEN" in readme
    assert (ROOT / ".env.example").is_file()
    assert "FINAINCE_DESK_TOKEN" in (ROOT / ".env.example").read_text()
    assert (ROOT / "NOTICE").is_file()
    assert "WeissHymmnos" in (ROOT / "NOTICE").read_text()
    assert "18702585ed19f50c85c9dd9b023ebff2a09a56f7" in pytest_wf
    assert "99e0924e41cc6c8d70d7670cdf1cf0c63bb3aeaa" in pytest_wf
    assert "fb5314833a816d40201fb1173a6afe6788dfba2c" in pytest_wf
    assert "ref: finaince-312" not in pytest_wf
    assert "ref: finaince-desk" not in pytest_wf
    assert "@finaince-desk" not in pyproject
    assert "@finaince-312" not in pyproject
    assert "allow-direct-references" in pyproject
    assert 'path = "../reproagent"' not in pyproject
    assert 'path = "../aiminer"' not in pyproject
    assert "uv pip install -e ../reproagent" not in readme
    assert "install -e ../aiminer" not in readme
    assert "SIBLING_CHECKOUT_TOKEN" not in readme
    assert "AGPL" in readme or "Affero" in readme
    assert "local_panel" in readme
    assert "finaince baseline" in readme
    assert "finaince impl examples/15min/compute.py" in readme
    assert "WeissHymmnos/finaince" in contributing
    assert 'uv pip install -e ".[reproduction,dev]"' in contributing
    assert (ROOT / "examples" / "15min" / "compute.py").is_file()


def test_locked_baseline_two_runs_agree(isolated_home: Path) -> None:
    first = run_locked_baseline()
    second = run_locked_baseline()
    assert first["window"] == second["window"] == dict(LOCKED_WINDOW)
    assert first["window"]["universe"] == "local_panel"
    assert first["window"]["cost_bps"] == 0
    assert first["window"]["expression"] == "Rank(Delta(close, 1))"
    assert first["ok"] == second["ok"]
    assert first["claim"] == second["claim"]
    assert first["claim"] == LOCKED_WINDOW["note"]
    assert "local_panel" in first["claim"]
    assert "0 bps" in first["claim"]
    assert "smoke" in first["claim"].lower()
    assert "research number" not in first["claim"].lower()
    assert "citable" not in first["claim"].lower()
    assert first["metrics"].get("ic_mean") == second["metrics"].get("ic_mean")
    assert first["metrics"].get("sharpe_ratio") == second["metrics"].get("sharpe_ratio")
    if first["ok"]:
        assert first["metrics"].get("universe_claim") == "local_panel"
    else:
        assert first.get("error") == second.get("error")


def test_locked_baseline_applies_zero_bps_not_engine_default(isolated_home: Path) -> None:
    """The public number must charge 0bps, not BacktestParams' 3.0 default."""
    import inspect

    from reproagent.models.replication import BacktestParams
    from reproagent.reproducer.backtest_bundle import build_backtest_bundle

    from finaince.eval.router import EvalRequest, evaluate

    fields = BacktestParams.model_fields
    assert "transaction_cost_bps" in fields
    assert float(fields["transaction_cost_bps"].default) == 3.0
    source = inspect.getsource(build_backtest_bundle)
    assert "transaction_cost_bps=cost" in source or "transaction_cost_bps=cost," in source
    assert "transaction_cost_bps" in inspect.signature(build_backtest_bundle).parameters

    out = run_locked_baseline()
    applied = out["metrics"].get("transaction_cost_bps")
    assert applied == 0, out
    assert applied != float(fields["transaction_cost_bps"].default)

    window = dict(LOCKED_WINDOW)
    zero = evaluate(
        EvalRequest(
            expression=str(window["expression"]),
            dialect=str(window["dialect"]),
            data_backend="local",
            universe=str(window["universe"]),
            start=str(window["start"]),
            end=str(window["end"]),
            cost_bps=0,
        )
    )
    three = evaluate(
        EvalRequest(
            expression=str(window["expression"]),
            dialect=str(window["dialect"]),
            data_backend="local",
            universe=str(window["universe"]),
            start=str(window["start"]),
            end=str(window["end"]),
            cost_bps=3,
        )
    )
    assert zero.metrics.get("transaction_cost_bps") == 0
    assert three.metrics.get("transaction_cost_bps") == 3
    if zero.ok and three.ok:
        assert zero.metrics.get("sharpe_ratio") != three.metrics.get("sharpe_ratio")


def test_promotion_gates_fail_closed_on_shipped_function(isolated_home: Path) -> None:
    proxy = _record(lineage=FactorLineage(source="manual", source_ref="p", formula_proxy=True))
    assert "formula_proxy" in evaluate_gates(proxy, direction="to_pool")["failures"]

    missing_ic = _record(metrics=FactorMetrics(ic=None))
    assert "missing_ic" in evaluate_gates(missing_ic, direction="to_pool")["failures"]

    missing_returns = _record(daily_returns={})
    assert "missing_returns" in evaluate_gates(missing_returns, direction="to_pool")["failures"]

    csi = _record(universe="csi300")
    failures = evaluate_gates(csi, direction="to_pool")["failures"]
    assert "thin_panel" in failures
    assert evaluate_gates(csi, direction="to_pool")["passed"] is False

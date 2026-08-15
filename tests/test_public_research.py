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
    assert "git+https://github.com/WeissHymmnos/ReproAgent.git" in pyproject
    assert "git+https://github.com/WeissHymmnos/aiminer.git@finaince-312" in pyproject
    assert 'path = "../reproagent"' not in pyproject
    assert 'path = "../aiminer"' not in pyproject
    assert "uv pip install -e ../reproagent" not in readme
    assert "install -e ../aiminer" not in readme
    assert "SIBLING_CHECKOUT_TOKEN" not in readme
    assert "AGPL" in readme or "Affero" in readme
    assert "not" in readme.lower() and "CSI300" in readme
    assert "ARR" in readme
    assert "finaince baseline" in readme
    assert "finaince impl examples/15min/compute.py" in readme
    assert "WeissHymmnos/finaince" in contributing
    assert "SIBLING_CHECKOUT_TOKEN" in contributing
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
    assert "not CSI300" in first["claim"]
    assert "ARR" in first["claim"]
    assert first["metrics"].get("ic_mean") == second["metrics"].get("ic_mean")
    assert first["metrics"].get("sharpe_ratio") == second["metrics"].get("sharpe_ratio")
    if first["ok"]:
        assert first["metrics"].get("universe_claim") == "local_panel"
    else:
        assert first.get("error") == second.get("error")


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

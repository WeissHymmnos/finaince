"""Remaining success paths: approve→pool, qlib child, packaged SPA, isolate eval."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from finaince.catalog.hooks import accept_library_entry
from finaince.catalog.store import FactorCatalog
from finaince.eval.router import EvalRequest, evaluate
from finaince.isolate import child_isolate, upsert_isolated
from finaince.serve import create_app
from tests.conftest import desk_headers

ROOT = Path(__file__).resolve().parents[1]


def _cli(home: Path, args: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FINAINCE_HOME"] = str(home)
    env["ALLOW_MOCK_LLM"] = "true"
    env["FINAINCE_DATA_SOURCE"] = "local"
    return subprocess.run(
        [sys.executable, "-m", "finaince", *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _json(text: str) -> dict:
    blob = text.strip()
    start = blob.find("{")
    return json.loads(blob[start:])


def test_cli_approve_override_writes_pool_code(isolated_home: Path) -> None:
    from aiminer.pool_io import load_alpha_pool_rows

    from finaince.settings import get_settings

    class _F:
        name = "cli_pool_mom"
        name_cn = "cli"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-cli-pool"

    class _E:
        id = "lib-cli-pool"
        report_id = "rep-cli-pool"
        factor = _F()

    returns = {f"2024-06-{d:02d}": 0.012 for d in range(1, 13)}
    accept_library_entry(
        _E(),
        extras={"metrics": {"ic_mean": 0.08}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "cli_pool_mom")
    promo = _json(_cli(isolated_home, ["promote", rec.id, "--to", "to_pool"]).stdout)
    assert promo["ok"] is True
    denied = _json(_cli(isolated_home, ["review", "--approve", promo["promotion_id"]]).stdout)
    assert denied["ok"] is False
    assert "thin_panel" in ((denied.get("gates") or {}).get("failures") or [])
    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    approved = _json(
        _cli(
            isolated_home,
            ["review", "--approve", promo["promotion_id"], "--override", "thin_panel,homogeneous"],
        ).stdout
    )
    assert approved["ok"] is True, approved
    rows = load_alpha_pool_rows(get_settings().aiminer_db)
    assert len(rows) > len(before)
    written = next(row for row in rows if row.get("code"))
    assert written["code"]
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    blocked = client.post(
        f"/api/v1/review/{promo['promotion_id']}/approve",
        json={"override": ["thin_panel"]},
        headers=desk_headers(),
    )
    assert blocked.status_code == 403


def test_qlib_cli_ok_on_local_panel(isolated_home: Path) -> None:
    out = _cli(
        isolated_home,
        ["eval", "Rank($close)", "--dialect", "qlib", "--backend", "local"],
    )
    assert out.returncode == 0, out.stderr + out.stdout
    body = _json(out.stdout)
    assert body["ok"] is True
    assert body.get("error") != "qlib_placeholder"
    assert isinstance((body.get("metrics") or {}).get("ic_mean"), (int, float))


def test_qlib_falls_back_when_local_parquet_lacks_datetime(
    isolated_home: Path, tmp_path: Path, monkeypatch
) -> None:
    import pandas as pd

    from finaince.runtime import packaged_local_panel, qlib_local_data_path

    bad = tmp_path / "tushare_panel"
    bad.mkdir()
    pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2005-01-04", "2005-01-05"]),
            "ts_code": ["000001.SZ", "000001.SZ"],
            "open": [1.0, 1.1],
            "high": [1.2, 1.2],
            "low": [0.9, 1.0],
            "close": [1.1, 1.05],
            "volume": [100.0, 110.0],
            "amount": [110.0, 115.0],
        }
    ).to_parquet(bad / "prices.parquet")
    monkeypatch.setenv("LOCAL_DATA_PATH", str(bad))
    monkeypatch.setenv("FINAINCE_LOCAL_DATA_PATH", str(bad))
    monkeypatch.delenv("FINAINCE_QLIB_SUBPROCESS", raising=False)
    chosen = qlib_local_data_path()
    packed = packaged_local_panel()
    assert packed is not None
    assert chosen == packed
    out = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib", data_backend="local"))
    assert out.ok is True, out
    assert out.error != "qlib_placeholder"
    assert out.metrics.get("via") == "qlib_child"
    assert isinstance(out.metrics.get("ic_mean"), (int, float))


def test_packaged_spa_without_sibling_dist(isolated_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("FINAINCE_PACKAGED_SPA", "1")
    import finaince.serve as serve_mod

    serve_mod.app = None
    dist = serve_mod.resolve_workbench_dist()
    assert dist is not None
    assert dist.name == "web"
    client = TestClient(create_app())
    page = client.get("/")
    assert page.status_code == 200
    assert "FinAlpha" in page.text
    assert 'id="pane-catalog"' in page.text and 'id="pane-review"' in page.text
    assert "frontend not built" not in page.text.lower()
    assert "spa disabled" not in page.text.lower()


def test_isolate_stores_shipped_eval_metrics(isolated_home: Path) -> None:
    expr = "Rank(Delta(close, 1))"
    child = child_isolate(
        {
            "source": (
                "NAME = 'iso_eval'\n"
                f"EXPRESSION = {expr!r}\n"
                "def compute(panel):\n"
                "    close = list(panel['close'])\n"
                "    return [0.0] + [close[i]-close[i-1] for i in range(1, len(close))]\n"
            ),
            "expression": expr,
        }
    )
    assert child.get("ok") is True, child
    stored = upsert_isolated(child, universe="local_panel")
    assert stored.get("ok") is True
    rec = stored["record"]
    ev = evaluate(EvalRequest(expression=expr, dialect="repro_polars", data_backend="local"))
    assert ev.ok is True
    assert rec.metrics.ic == ev.metrics.get("ic_mean")
    assert rec.daily_returns == (ev.metrics.get("daily_returns") or {})
    assert not any(str(k).startswith("2024-01-") for k in rec.daily_returns)
    assert rec.is_simulated is False


def test_reproduce_discover_demo_and_agent_json(
    isolated_home: Path, sample_report_path: Path
) -> None:
    repro = _cli(isolated_home, ["reproduce", str(sample_report_path), "--sync"], timeout=60)
    assert repro.returncode == 0, repro.stderr + repro.stdout
    body = _json(repro.stdout)
    assert body.get("status")

    demo = _cli(isolated_home, ["discover", "--demo"])
    assert demo.returncode == 0, demo.stderr + demo.stdout
    demo_body = _json(demo.stdout)
    assert demo_body.get("kept_count") is not None
    assert int(demo_body["kept_count"]) >= 1

    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    posted = client.post(
        "/api/v1/agent",
        json={"prompt": "doctor then catalog_list", "max_turns": 2},
        headers=desk_headers(),
    )
    assert posted.status_code < 500
    payload = posted.json()
    assert isinstance(payload.get("ok"), bool)

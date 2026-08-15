"""Drive shipped catalog/eval/review/HTTP contracts from v2 plan."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finaince.catalog.hooks import accept_library_entry
from finaince.catalog.store import FactorCatalog
from finaince.eval.router import EvalRequest, evaluate
from finaince.review.desk import approve, promote, reject
from finaince.serve import create_app
from finaince.tools import handle_search_library


def _entry(name: str = "v2_mom", style: str = "momentum"):
    class _F:
        pass

    f = _F()
    f.name = name
    f.name_cn = name
    f.style = style
    f.formula = "close / Ref(close, 5) - 1"
    f.input_fields = ["close"]
    f.universe = "local_panel"
    f.rebalance_frequency = "daily"
    f.spec_id = "spec-v2"

    class _E:
        pass

    e = _E()
    e.id = f"lib-{name}"
    e.report_id = f"rep-{name}"
    e.factor = f
    return e


def test_catalog_first_search_filters_style(isolated_home: Path) -> None:
    returns = {f"2024-01-{d:02d}": 0.01 for d in range(2, 14)}
    accept_library_entry(
        _entry("style_mom", "momentum"),
        extras={"metrics": {"ic_mean": 0.04}, "daily_returns": returns, "observability": {}},
    )
    accept_library_entry(
        _entry("style_val", "value"),
        extras={"metrics": {"ic_mean": 0.03}, "daily_returns": returns, "observability": {}},
    )
    mom = handle_search_library(query="", style="momentum")
    assert mom["count"] >= 1
    assert all(item.get("style") == "momentum" for item in mom["items"] if item.get("catalog_id"))
    assert any(item.get("name") == "style_mom" for item in mom["items"])


def test_reject_then_promote_again(isolated_home: Path) -> None:
    returns = {f"2024-02-{d:02d}": 0.02 for d in range(1, 13)}
    accept_library_entry(
        _entry("rej_cycle"),
        extras={"metrics": {"ic_mean": 0.05}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "rej_cycle")
    first = promote(rec.id, direction="to_pool")
    assert first["ok"] is True
    denied = reject(first["promotion_id"])
    assert denied["ok"] is True
    again = FactorCatalog().get(rec.id)
    assert again is not None
    assert again.status == "candidate"
    second = promote(rec.id, direction="to_pool")
    assert second["ok"] is True
    assert second["promotion_id"] != first["promotion_id"]


def test_eval_snapshot_compares_shipped_bundle(isolated_home: Path) -> None:
    import os

    from finaince.eval.snapshot import run_snapshot

    out = run_snapshot()
    assert "items" in out
    assert len(out["items"]) == 3
    assert out["ok"] is True


def test_qlib_placeholder_is_not_ok(isolated_home: Path) -> None:
    out = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert out.ok is False
    assert out.error == "qlib_placeholder"


def test_thin_panel_gate_only_on_csi300_claim(isolated_home: Path) -> None:
    from finaince.review.gates import evaluate_gates

    returns = {f"2024-03-{d:02d}": 0.01 for d in range(1, 13)}
    accept_library_entry(
        _entry("thin_claim"),
        extras={"metrics": {"ic_mean": 0.06}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "thin_claim")
    rec.universe = "csi300"
    FactorCatalog().upsert(rec)
    gates = evaluate_gates(rec, direction="to_pool")
    assert "thin_panel" in gates["failures"]
    skipped = evaluate_gates(rec, direction="to_pool", override=["thin_panel"])
    assert "thin_panel" not in skipped["failures"]
    rec.universe = "local_panel"
    FactorCatalog().upsert(rec)
    local = evaluate_gates(rec, direction="to_pool")
    assert "thin_panel" not in local["failures"]


def _assert_workbench_index(response) -> str:
    assert response.status_code == 200, response.text
    text = response.text
    assert "FinAlpha" in text
    assert "spa disabled" not in text.lower()
    assert "frontend not built" not in text.lower()
    assert 'id="root"' in text
    return text


def test_serve_index_and_health(isolated_home: Path) -> None:
    import re

    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["product"] == "FinAlpha"
    assert isinstance(body["ok"], bool)
    assert isinstance(body["degraded"], bool)
    text = _assert_workbench_index(client.get("/"))
    srcs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', text)
    assert srcs, "workbench index must reference hashed /assets/*"
    asset = client.get(srcs[0])
    assert asset.status_code == 200
    assert len(asset.content) > 100
    dist = serve_mod.resolve_workbench_dist()
    assert dist is not None
    assert (dist / "index.html").is_file()
    assert "FinAlpha" in (dist / "index.html").read_text(encoding="utf-8")
    catalog_js = next((dist / "assets").glob("CatalogPage-*.js"), None)
    assert catalog_js is not None
    catalog_src = catalog_js.read_text(encoding="utf-8")
    assert "/api/v1/catalog" in catalog_src
    assert "localhost:8000" not in catalog_src


def test_serve_spa_when_aiminer_api_missing(isolated_home: Path, monkeypatch) -> None:
    import sys
    import types

    import finaince.serve as serve_mod

    class Boom(types.ModuleType):
        def __getattr__(self, name: str) -> None:
            raise ImportError("forced: aiminer.api unavailable")

    monkeypatch.setitem(sys.modules, "aiminer.api", Boom("aiminer.api"))
    serve_mod.app = None
    client = TestClient(create_app())
    _assert_workbench_index(client.get("/"))
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["product"] == "FinAlpha"
    assert isinstance(health.json()["ok"], bool)
    assert isinstance(health.json()["degraded"], bool)
    api_health = client.get("/api/health")
    assert api_health.status_code == 200
    assert api_health.headers.get("content-type", "").startswith("application/json")
    _assert_workbench_index(client.get("/review"))


def test_http_detail_reject_jobs_qlib(isolated_home: Path) -> None:
    returns = {f"2024-04-{d:02d}": 0.01 for d in range(1, 13)}
    accept_library_entry(
        _entry("http_row"),
        extras={"metrics": {"ic_mean": 0.07}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "http_row")
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    posted = client.post("/api/v1/promote", json={"catalog_id": rec.id, "direction": "to_pool"})
    assert posted.status_code == 200
    promo = posted.json()
    assert promo["ok"] is True
    detail = client.get(f"/api/v1/catalog/{rec.id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == rec.id
    qlib = client.post("/api/v1/eval", json={"expression": "Rank($close)", "dialect": "qlib"})
    assert qlib.status_code == 200
    assert qlib.json()["ok"] is False
    rejected = client.post(f"/api/v1/review/{promo['promotion_id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["ok"] is True
    after = FactorCatalog().get(rec.id)
    assert after is not None and after.status == "candidate"
    again = client.post("/api/v1/promote", json={"catalog_id": rec.id, "direction": "to_pool"})
    assert again.status_code == 200 and again.json()["ok"] is True
    approved = client.post(
        f"/api/v1/review/{again.json()['promotion_id']}/approve",
        json={"override": ["thin_panel"]},
    )
    assert approved.status_code == 200
    from finaince.jobs.runner import submit

    job = submit("evaluate", {"expr": "x"}, run=lambda: {"ok": True})
    got = client.get(f"/api/v1/jobs/{job['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == job["id"]


def test_frontend_review_has_reject_and_job_poll() -> None:
    root = Path(__file__).resolve().parents[2]
    review = (root / "aiminer" / "frontend" / "src" / "pages" / "ReviewPage.tsx").read_text()
    repro = (root / "aiminer" / "frontend" / "src" / "pages" / "ReproducePage.tsx").read_text()
    app = (root / "aiminer" / "frontend" / "src" / "App.tsx").read_text()
    assert "/api/v1/review/" in review and "reject" in review
    assert "gates" in review
    assert "/api/v1/jobs/" in repro
    assert 'path="/catalog/:id"' in app
    catalog = (root / "aiminer" / "frontend" / "src" / "pages" / "CatalogPage.tsx").read_text()
    agent = (root / "aiminer" / "frontend" / "src" / "pages" / "AgentPage.tsx").read_text()
    assert "/api/v1/catalog/${id}" in catalog or "/api/v1/catalog/" in catalog
    assert "useParams" in catalog
    assert "/api/v1/promote" in catalog
    assert "override" in review
    assert "thin_panel" in review
    assert "no_factors" in repro
    assert "ok === false" in agent or "body.ok === false" in agent


def test_qlib_placeholder_and_fake_subprocess(isolated_home: Path, monkeypatch, tmp_path: Path) -> None:
    from finaince.eval.qlib_subprocess import compare_engines
    from finaince.eval.router import EvalRequest, evaluate

    monkeypatch.delenv("FINAINCE_QLIB_SUBPROCESS", raising=False)
    monkeypatch.delenv("FINAINCE_QLIB_PLACEHOLDER_OK", raising=False)
    off = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert off.ok is False
    assert off.error == "qlib_placeholder"
    skipped = compare_engines("Rank(Delta(close, 1))")
    assert skipped["ok"] is False
    assert skipped["skipped"] is True
    assert skipped["qlib"] is None

    fake = tmp_path / "fake-python"
    fake.write_text("#!/bin/sh\necho not-json\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("FINAINCE_QLIB_SUBPROCESS", "1")
    monkeypatch.setenv("AIMINER_PYTHON", str(fake))
    on = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert on.ok is False
    assert on.error
    assert on.error != "qlib_placeholder"
    monkeypatch.setenv("AIMINER_PYTHON", str(tmp_path / "no-such-python"))
    gone = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert gone.ok is False
    assert gone.error == "qlib_subprocess_missing_python"


def test_child_qlib_eval_uses_build_evaluator_run(isolated_home: Path) -> None:
    from finaince.eval.qlib_subprocess import child_qlib_eval

    fixture = (
        Path(__file__).resolve().parents[2]
        / "reproagent"
        / "tests"
        / "fixtures"
        / "test_data"
        / "prices.parquet"
    )
    out = child_qlib_eval(
        {
            "expression": "Rank($close)",
            "data_backend": "local",
            "local_data_path": str(fixture),
            "local_data_layout": "panel",
            "start": "2023-01-02",
            "end": "2023-02-10",
        }
    )
    assert out["ok"] is True, out
    assert out["metrics"].get("ic_mean") is not None
    assert out["metrics"].get("via") == "qlib_child"


def test_run_qlib_eval_spawns_aiminer_child(isolated_home: Path, monkeypatch) -> None:
    import sys

    from finaince.eval.qlib_subprocess import aiminer_src, run_qlib_eval

    fixture = (
        Path(__file__).resolve().parents[2]
        / "reproagent"
        / "tests"
        / "fixtures"
        / "test_data"
        / "prices.parquet"
    )
    monkeypatch.setenv("FINAINCE_QLIB_SUBPROCESS", "1")
    monkeypatch.setenv("AIMINER_PYTHON", sys.executable)
    monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
    assert (aiminer_src() / "aiminer" / "core" / "qlib_child.py").is_file()
    out = run_qlib_eval(
        "Rank($close)",
        start="2023-01-02",
        end="2023-02-10",
        data_backend="local",
        local_data_path=str(fixture),
        python=sys.executable,
    )
    assert out.get("ok") is True, out
    assert out["metrics"].get("ic_mean") is not None
    assert out["metrics"].get("via") == "qlib_child"
    assert "No module named 'aiminer'" not in str(out.get("error") or "")


def test_doctor_watch_and_retag_synthetic(isolated_home: Path) -> None:
    from typer.testing import CliRunner

    from finaince.catalog.hooks import accept_pool_row
    from finaince.catalog.rebuild import retag_synthetic
    from finaince.catalog.store import FactorCatalog
    from finaince.cli import app

    watch = CliRunner().invoke(app, ["doctor", "--watch", "--iterations", "1", "--interval", "0"])
    assert watch.exit_code == 0, watch.output
    body = __import__("json").loads(watch.stdout)
    assert body["product_name"] == "FinAlpha"
    assert body["watch"] is True
    assert body["tick"] == 1

    returns = {f"2024-09-{d:02d}": 0.01 for d in range(1, 13)}
    accept_pool_row(
        {
            "id": "alpha_retag",
            "hypothesis": "disc_retag",
            "code": "Rank($close)",
            "ic": 0.09,
            "returns": returns,
        }
    )
    rec = next(r for r in FactorCatalog().list(source="discovery") if r.name == "disc_retag")
    assert "synthetic" not in rec.tags
    cli = CliRunner().invoke(app, ["catalog", "rebuild", "--retag-synthetic"])
    assert cli.exit_code == 0, cli.output
    out = __import__("json").loads(cli.stdout)
    assert out["ok"] is True
    assert out["catalog_tagged"] >= 1
    after = FactorCatalog().get(rec.id)
    assert after is not None
    assert "synthetic" in after.tags
    assert "source:discovery" in after.tags
    shipped = retag_synthetic()
    assert shipped["ok"] is True


def test_eval_exposes_alt_text(isolated_home: Path) -> None:
    out = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    assert out.translatable is True
    assert out.alt_text
    assert "$close" in out.alt_text


def test_metrics_jsonl_and_health_degraded(isolated_home: Path) -> None:
    from finaince.jobs.runner import submit
    from finaince.obs import emit, jobs_degraded
    from finaince.serve import create_app

    emit("eval_finished", dialect="repro_polars", data_backend="local", ok=True)
    metrics = isolated_home / "logs" / "metrics.jsonl"
    assert metrics.is_file()
    lines = metrics.read_text(encoding="utf-8").strip().splitlines()
    assert any("eval_finished" in line for line in lines)

    def _boom() -> dict:
        raise RuntimeError("forced")

    for _ in range(3):
        submit("evaluate", {"expr": "x"}, run=_boom)
    assert jobs_degraded() is True
    import finaince.serve as serve_mod

    serve_mod.app = None
    health = TestClient(create_app()).get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["degraded"] is True
    assert body["ok"] is False


def test_rq_cache_roots_follow_finaince_home(isolated_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("FINAINCE_HOME", str(isolated_home))
    from reproagent.reproducer.data_loader import _rq_cache_roots

    prices, instruments = _rq_cache_roots()
    assert prices == isolated_home / "reproagent" / "cache" / "ricequant_prices"
    assert instruments == isolated_home / "reproagent" / "cache" / "ricequant_instruments"


def test_to_library_writes_synthetic_report(isolated_home: Path) -> None:
    from finaince.catalog.hooks import accept_pool_row
    from finaince.catalog.store import FactorCatalog
    from reproagent.persistence.db import get_engine
    from reproagent.persistence.repository import Repository
    from reproagent.settings import Settings

    returns = {f"2024-07-{d:02d}": 0.03 for d in range(1, 13)}
    accept_pool_row(
        {
            "id": "alpha_synth01",
            "hypothesis": "disc_synth",
            "code": "Rank($close)",
            "ic": 0.08,
            "returns": returns,
        }
    )
    rec = next(r for r in FactorCatalog().list(source="discovery") if r.name == "disc_synth")
    promo = promote(rec.id, direction="to_library")
    assert promo["ok"] is True
    result = approve(promo["promotion_id"])
    assert result["ok"] is True, result
    after = FactorCatalog().get(rec.id)
    assert after is not None
    assert "synthetic" in after.tags
    assert "source:discovery" in after.tags
    md = isolated_home / "reproagent" / "reports" / "synthetic" / f"{after.lineage.source_ref}.md"
    assert md.is_file()
    repo = Repository(get_engine(Settings(data_dir=isolated_home / "reproagent").db_path))
    report = repo.get_report(after.lineage.report_id or f"disc_{after.lineage.source_ref}")
    assert report is not None
    assert report.validation_status in {"synthetic", "valid"}
    assert report.broker == "finaince-discovery"


def test_audit_rows_written_on_review_and_eval(isolated_home: Path) -> None:
    from finaince.catalog.audit import list_audit
    from finaince.catalog.hooks import accept_library_entry

    returns = {f"2024-08-{d:02d}": 0.01 for d in range(1, 13)}
    accept_library_entry(
        _entry("audit_mom"),
        extras={"metrics": {"ic_mean": 0.06}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "audit_mom")
    promo = promote(rec.id, direction="to_pool")
    reject(promo["promotion_id"])
    evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    actions = {row["action"] for row in list_audit()}
    assert {"promote", "reject", "eval"} <= actions
    assert all(row.get("hash") for row in list_audit())


def test_submit_updates_claimed_parent_job(isolated_home: Path, monkeypatch) -> None:
    from finaince.catalog.audit import list_audit
    from finaince.jobs.runner import get_job, list_jobs, submit

    parent = submit("reproduce_report", {"pdf_path": "x"})
    assert parent["status"] == "queued"
    monkeypatch.setenv("FINAINCE_JOB_ID", parent["id"])
    done = submit(
        "reproduce_report",
        {"pdf_path": "x"},
        run=lambda: {"status": "no_factors", "factors": []},
    )
    assert done["id"] == parent["id"]
    assert done["status"] == "done"
    assert done["result"]["status"] == "no_factors"
    again = get_job(parent["id"])
    assert again is not None and again["status"] == "done"
    assert again["result"]["status"] == "no_factors"
    assert sum(1 for row in list_jobs() if row["kind"] == "reproduce_report") == 1
    finished = [row for row in list_audit() if row["action"] == "job_finished"]
    assert any(row["detail"].get("job_id") == parent["id"] for row in finished)


def test_async_child_writes_parent_job_terminal(isolated_home: Path) -> None:
    import sys
    import time

    from finaince.jobs.runner import get_job, start_process

    code = (
        "from finaince.jobs.runner import submit\n"
        "submit('reproduce_report', {}, run=lambda: {'status': 'no_factors', 'factors': []})\n"
    )
    job = start_process(
        "reproduce_report",
        {"pdf_path": "x"},
        [sys.executable, "-c", code],
    )
    assert job["status"] in {"queued", "running"}
    parent_id = job["id"]
    row = None
    deadline = time.time() + 20
    while time.time() < deadline:
        row = get_job(parent_id)
        if row and row.get("status") not in {"running", "queued"}:
            break
        time.sleep(0.1)
    assert row is not None
    assert row["id"] == parent_id
    assert row["status"] == "done"
    assert row["result"]["status"] == "no_factors"


def test_http_async_reproduce_polls_parent_to_terminal(
    isolated_home: Path, sample_report_path: Path
) -> None:
    import time

    from fastapi.testclient import TestClient

    from finaince.serve import create_app
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    posted = client.post(
        "/api/v1/reproduce",
        json={"pdf_path": str(sample_report_path), "sync": False},
    )
    assert posted.status_code == 200
    body = posted.json()
    job_id = body.get("id")
    assert job_id
    assert body.get("status") in {"queued", "running"}
    row = None
    deadline = time.time() + 90
    while time.time() < deadline:
        got = client.get(f"/api/v1/jobs/{job_id}")
        assert got.status_code == 200
        row = got.json()
        if row.get("status") not in {"running", "queued"}:
            break
        time.sleep(0.25)
    assert row is not None
    assert row["id"] == job_id
    assert row["status"] in {"done", "error"}
    if row["status"] == "done":
        result = row.get("result") or {}
        assert result.get("status") or result.get("factors") is not None


def test_eval_equity_curve_has_ls_returns_on_thin_panel(isolated_home: Path) -> None:
    import os

    from finaince.settings import get_settings
    from reproagent.reproducer.metrics import serialize_equity_returns

    out = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    assert out.ok is True
    assert int(out.metrics.get("rows") or 0) > 0
    home = get_settings().repro_data_dir
    curves = list(home.joinpath("backtest").rglob("equity_curve.parquet"))
    assert curves
    returns = {}
    for path in curves:
        returns.update(serialize_equity_returns(path))
    assert returns, "thin 2-asset panel must still emit long-short daily returns"


def test_cli_reproduce_writes_catalog_returns(
    isolated_home: Path, sample_report_path: Path
) -> None:
    import finreportparser.output  # noqa: F401
    from typer.testing import CliRunner

    from finaince.cli import app

    result = CliRunner().invoke(
        app,
        ["reproduce", str(sample_report_path), "--sync", "--source", "local"],
    )
    assert result.exit_code == 0, result.output
    recs = FactorCatalog().list(source="reproduction")
    assert recs, result.output
    rec = recs[0]
    assert rec.daily_returns, "passed reproduce must dual-write daily returns"
    assert rec.universe == "local_panel"
    assert rec.metrics.ic is not None


def test_get_job_reaps_dead_async_child(isolated_home: Path) -> None:
    import sys
    import time

    from finaince.jobs.runner import get_job, start_process

    job = start_process(
        "sleep",
        {},
        [sys.executable, "-c", "raise SystemExit(0)"],
    )
    row = None
    deadline = time.time() + 10
    while time.time() < deadline:
        row = get_job(job["id"])
        if row and row.get("status") not in {"running", "queued"}:
            break
        time.sleep(0.05)
    assert row is not None
    assert row["status"] == "error"
    assert row["error"] == "child_exited"

"""Drive shipped catalog, gates, eval, scoring, serve routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from finaince.cli import app
from finaince.domain.scoring import named_score
from finaince.review.desk import approve, promote
from finaince.tools import handle_score_factor

runner = CliRunner()


def test_catalog_upserts_both_sources(isolated_home: Path) -> None:
    from finaince.catalog.hooks import accept_pool_row
    from finaince.catalog.store import FactorCatalog

    returns = {f"2024-01-{d:02d}": 0.01 for d in range(1, 13)}
    accept_pool_row(
        {
            "id": "alpha_disc01",
            "hypothesis": "disc-factor",
            "role": "momentum",
            "code": "Rank($close)",
            "perf_metric": 0.04,
            "returns": returns,
            "metrics": {"information_coefficient": 0.04},
        }
    )
    # incomplete extras must not upsert
    class _F:
        name = "repro-factor"
        name_cn = "复现"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "csi300"
        rebalance_frequency = "daily"
        spec_id = "s1"

    class _E:
        id = "lib-repro01"
        report_id = "rep1"
        factor = _F()

    from finaince.catalog.hooks import accept_library_entry

    accept_library_entry(_E(), extras={"metrics": {}})
    cat = FactorCatalog()
    assert not any(r.lineage.source_ref == "lib-repro01" for r in cat.list())

    accept_library_entry(
        _E(),
        extras={
            "metrics": {"ic_mean": 0.03, "sharpe_ratio": 1.1},
            "daily_returns": returns,
            "observability": {"formula_proxy": False},
        },
    )
    sources = {r.lineage.source for r in cat.list()}
    assert "discovery" in sources
    assert "reproduction" in sources


def test_catalog_disabled_skips(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINAINCE_CATALOG", "0")
    from finaince.catalog.hooks import accept_pool_row
    from finaince.catalog.store import FactorCatalog

    accept_pool_row(
        {
            "id": "alpha_off",
            "hypothesis": "off",
            "returns": {"2024-01-01": 0.01},
            "perf_metric": 0.04,
        }
    )
    assert FactorCatalog().list() == []


def test_library_entry_proxy_flag_is_per_factor(isolated_home: Path) -> None:
    from finaince.catalog.hooks import accept_library_entry
    from finaince.catalog.store import FactorCatalog

    class _F:
        name = "price_momentum"
        name_cn = "价格动量"
        style = "momentum"
        formula = "close / Ref(close, 12) - 1"
        input_fields = ["close"]
        universe = "csi300"
        rebalance_frequency = "monthly"
        spec_id = "s-mom"

    class _E:
        id = "lib-scope-01"
        report_id = "rep-scope"
        factor = _F()

    returns = {f"2024-01-{d:02d}": 0.01 for d in range(2, 14)}
    accept_library_entry(
        _E(),
        extras={
            "metrics": {"ic_mean": 0.02},
            "daily_returns": returns,
            "observability": {
                "formula_proxy": True,
                "proxy_factors": ["PriceMomentumTValue"],
            },
        },
    )
    rec = next(r for r in FactorCatalog().list() if r.lineage.source_ref == "lib-scope-01")
    assert rec.lineage.formula_proxy is False


def test_promote_pending_approve_fail_closed(isolated_home: Path) -> None:
    from finaince.catalog.hooks import accept_pool_row
    from finaince.catalog.store import FactorCatalog

    accept_pool_row(
        {
            "id": "alpha_empty",
            "hypothesis": "no-returns-after",
            "code": "Rank($close)",
            "perf_metric": 0.04,
            "returns": {f"2024-01-{d:02d}": 0.01 for d in range(1, 13)},
            "metrics": {"information_coefficient": 0.04},
        }
    )
    rec = FactorCatalog().list()[0]
    rec.daily_returns = {}
    rec.metrics.ic = None
    FactorCatalog().upsert(rec)
    out = promote(rec.id, direction="to_pool")
    assert out["ok"] is True
    assert out["status"] == "review"
    denied = approve(out["promotion_id"])
    assert denied["ok"] is False
    assert denied.get("error") in {"gates_failed", "empty_returns"}


def test_approve_reproduction_to_pool_writes_nonempty_code(isolated_home: Path) -> None:
    from aiminer.pool_io import load_alpha_pool_rows

    from finaince.catalog.hooks import accept_library_entry
    from finaince.catalog.store import FactorCatalog
    from finaince.settings import get_settings

    class _F:
        name = "repro_momentum"
        name_cn = "动量"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-repro"

    class _E:
        id = "lib-crossfeed-01"
        report_id = "rep-crossfeed"
        factor = _F()

    returns = {f"2024-04-{d:02d}": 0.015 * ((-1) ** d) for d in range(1, 13)}
    accept_library_entry(
        _E(),
        extras={
            "metrics": {"ic_mean": 0.04, "sharpe_ratio": 1.2, "max_drawdown": 0.08},
            "daily_returns": returns,
            "observability": {"formula_proxy": False},
        },
    )
    recs = [r for r in FactorCatalog().list() if r.lineage.source_ref == "lib-crossfeed-01"]
    assert recs
    rec = recs[0]
    assert rec.lineage.source == "reproduction"
    assert rec.expression.dialect == "repro_polars"
    assert rec.expression.alt_text == "Rank(Delta($close, 1))"
    assert rec.expression.translatable is True
    assert rec.expression.text == "Rank(Delta(close, 1))"

    submitted = promote(rec.id, direction="to_pool")
    assert submitted["ok"] is True
    assert submitted["status"] == "review"
    result = approve(submitted["promotion_id"], override=["thin_panel", "homogeneous"])
    assert result["ok"] is True, result
    assert result["status"] == "ready"

    settings = get_settings()
    rows = load_alpha_pool_rows(settings.aiminer_db)
    assert rows
    written = next(row for row in rows if row.get("hypothesis") in {"repro_momentum", rec.name})
    assert written.get("code")
    assert written["code"] == "Rank(Delta($close, 1))"
    assert written.get("returns")


def test_named_scorers_are_distinct() -> None:
    metrics = {
        "annualized_return": 0.2,
        "sharpe": 1.5,
        "max_drawdown": 0.1,
        "turnover": 0.2,
        "cost_drag": 0.01,
    }
    sel = named_score("selection_score", metrics=metrics, factor_ic=0.05)
    grade = named_score("library_grade", ic_mean=0.05, sharpe=1.5, max_drawdown=0.1)
    assert sel["scorer"] == "selection_score"
    assert grade["scorer"] == "library_grade"
    assert sel["score"] != grade["score"]
    via_tools = handle_score_factor(metrics=metrics, factor_ic=0.05)
    assert via_tools["scorer"] == "selection_score"
    assert via_tools["score"] == sel["score"]


def test_frontend_default_route_is_catalog() -> None:
    from finaince.runtime import documents_root

    fe = documents_root() / "aiminer" / "frontend" / "src"
    app_src = (fe / "App.tsx").read_text()
    layout = (fe / "components" / "Layout.tsx").read_text()
    assert 'path="/" element={<CatalogPage />}' in app_src
    assert 'path="/catalog/:id"' in app_src
    assert 'path="/review" element={<ReviewPage />}' in app_src
    assert 'path="/reproduce" element={<ReproducePage />}' in app_src
    assert 'path="/agent" element={<AgentPage />}' in app_src
    assert 'path="/runs" element={<SwarmRunsPage />}' in app_src
    assert 'label: "Catalog"' in layout
    assert 'label: "Review"' in layout
    assert 'label: "Reproduce"' in layout
    assert "FinAlpha" in layout
    assert (fe / "pages" / "ReviewPage.tsx").is_file()
    assert (fe / "pages" / "ReproducePage.tsx").is_file()
    assert (fe / "pages" / "AgentPage.tsx").is_file()


def test_fastmcp_score_factor_is_library_grade_not_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reproagent.mcp_server import library_grade_impl
    from reproagent.settings import get_settings

    fixture = Path(__file__).resolve().parents[2] / "reproagent" / "tests" / "fixtures" / "test_data"
    monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
    get_settings.cache_clear()
    metrics_only = handle_score_factor(
        metrics={"annualized_return": 0.2, "sharpe": 1.0, "max_drawdown": 0.1}
    )
    graded = library_grade_impl("Rank(Delta(close, 1))")
    assert metrics_only["scorer"] == "selection_score"
    assert graded.get("scorer") == "library_grade" or "grade" in graded
    assert "grade" in graded
    assert graded.get("score") != metrics_only.get("score")


def test_pandas_to_polars_coerces_nullable_int() -> None:
    """Shipped ricequant helper must accept pandas Int64 without hanging on pyarrow."""
    import pandas as pd
    import polars as pl
    from reproagent.reproducer.data_loader import _pandas_to_polars

    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "ts_code": ["000001.XSHE", "000001.XSHE"],
            "close": pd.Series([10.0, 10.5], dtype="Float64"),
            "volume": pd.Series([100, 110], dtype="Int64"),
        }
    )
    out = _pandas_to_polars(frame)
    assert isinstance(out, pl.DataFrame)
    assert out.height == 2
    assert {"trade_date", "ts_code", "close", "volume"} <= set(out.columns)
    assert float(out["close"][1]) == 10.5


def test_eval_router_repro_polars(monkeypatch: pytest.MonkeyPatch) -> None:

    from reproagent.settings import get_settings

    from finaince.eval.router import EvalRequest, evaluate

    fixture = Path(__file__).resolve().parents[2] / "reproagent" / "tests" / "fixtures" / "test_data"
    monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
    get_settings.cache_clear()
    ok = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    bad = evaluate(EvalRequest(expression="Nope(close)", dialect="repro_polars"))
    assert ok.ok is True
    assert bad.ok is False
    assert ok.metrics.get("validation", {}).get("valid") is True
    numeric = [ok.metrics.get("ic_mean"), ok.metrics.get("sharpe_ratio"), ok.metrics.get("max_drawdown")]
    assert any(isinstance(v, (int, float)) and v is not None for v in numeric)


def test_inject_llm_env_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    from finaince.runtime import inject_llm_env

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    inject_llm_env(
        {
            "via": "gateway",
            "aiminer_provider": "openai",
            "api_key": "gateway-token",
            "base_url": "http://127.0.0.1:8317/v1",
        }
    )
    import os

    assert os.environ["LLM_API_KEY"] == "gateway-token"
    assert os.environ.get("DEEPSEEK_API_KEY") != "gateway-token"
    inject_llm_env(
        {
            "via": "deepseek",
            "aiminer_provider": "deepseek",
            "api_key": "sk-ds",
            "base_url": "https://api.deepseek.com/v1",
        }
    )
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-ds"


def test_resolve_llm_is_not_hardcoded_to_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    from finaince.runtime import resolve_llm

    monkeypatch.setattr("finaince.runtime.load_engine_dotenv", lambda: None)
    for key in (
        "FINAINCE_LLM_PROVIDER",
        "FINAINCE_LLM_MODEL",
        "FINAINCE_LLM_BASE_URL",
        "FINAINCE_LLM_API_KEY",
        "LLM_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "CPA_API_KEY",
        "DEEPSEEK_API_KEY",
        "Deepseek_KEY",
        "OPENAI_API_KEY",
        "OpenAI_KEY",
        "ANTHROPIC_API_KEY",
        "ClaudeCode_KEY",
        "GLM_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    missing = resolve_llm()
    assert missing["via"] == "missing"
    assert missing["model"] == ""

    monkeypatch.setenv("FINAINCE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FINAINCE_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("FINAINCE_LLM_API_KEY", "sk-test")
    openai = resolve_llm()
    assert openai["via"] == "openai"
    assert openai["aiminer_provider"] == "openai"
    assert openai["model"] == "gpt-4o-mini"
    assert "deepseek" not in (openai["base_url"] or "")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "local-gw")
    monkeypatch.delenv("FINAINCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FINAINCE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("FINAINCE_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("FINAINCE_LLM_MODEL", "my-local-model")
    gw = resolve_llm()
    assert gw["via"] == "gateway"
    assert gw["model"] == "my-local-model"
    assert gw["aiminer_provider"] == "openai"


def test_wiki_embedding_stays_local_for_chat_providers() -> None:
    from aiminer.core.embeddings import resolve_embedding_backend

    backend = resolve_embedding_backend("deepseek")
    assert backend["mode"] == "local"
    assert "bge" in backend["model_name"].lower()
    assert backend.get("api_base") in {None, ""}


def test_swarm_argv_follows_resolved_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from finaince.settings import swarm_argv

    monkeypatch.setattr("finaince.runtime.load_engine_dotenv", lambda: None)
    for key in (
        "FINAINCE_LLM_PROVIDER",
        "FINAINCE_LLM_MODEL",
        "FINAINCE_LLM_BASE_URL",
        "FINAINCE_LLM_API_KEY",
        "LLM_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "CPA_API_KEY",
        "DEEPSEEK_API_KEY",
        "Deepseek_KEY",
        "OPENAI_API_KEY",
        "ClaudeCode_KEY",
        "GLM_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    bare = swarm_argv([])
    assert "--mode" in bare and "ricequant" in bare
    assert "--embedding-provider" in bare and "local" in bare
    assert "deepseek" not in bare

    monkeypatch.setenv("FINAINCE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FINAINCE_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("FINAINCE_LLM_API_KEY", "sk-test")
    argv = swarm_argv([])
    assert argv[argv.index("--llm-provider") + 1] == "openai"
    assert "gpt-4o-mini" in argv
    assert "deepseek" not in argv
    custom = swarm_argv(["--iterations", "1", "--llm-model", "kept-model"])
    assert custom.count("--llm-model") == 1
    assert "kept-model" in custom
    assert "--iterations" in custom


def test_doctor_and_help_commands() -> None:
    import json

    help_r = runner.invoke(app, ["--help"])
    assert help_r.exit_code == 0
    assert "FinAlpha" in help_r.stdout
    for name in ("catalog", "eval", "promote", "review", "jobs", "doctor", "serve", "agent"):
        assert name in help_r.stdout
    doc = runner.invoke(app, ["doctor"])
    body = json.loads(doc.stdout)
    assert doc.exit_code == 0 if body.get("ok") else doc.exit_code != 0
    assert body["product_name"] == "FinAlpha"
    assert "home" in body
    assert "imports" in body
    assert "issues" in body
    assert "path_hack" in body
    assert set(body["imports"]) >= {"finaince", "aiminer", "reproagent"}


def test_doctor_exit_follows_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from finaince import settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "doctor_report",
        lambda **_k: {"ok": False, "home": "x", "imports": {}, "issues": ["boom"]},
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["issues"]


def test_serve_health_and_catalog_and_aiminer_prefix(isolated_home: Path) -> None:
    from fastapi.testclient import TestClient

    from finaince.serve import create_app

    client = TestClient(create_app())
    h = client.get("/api/v1/health")
    assert h.status_code == 200
    assert h.json()["product"] == "FinAlpha"
    assert isinstance(h.json()["ok"], bool)
    assert isinstance(h.json()["degraded"], bool)
    root = client.get("/")
    assert root.status_code == 200
    assert "FinAlpha" in root.text
    assert "spa disabled" not in root.text.lower()
    assert "frontend not built" not in root.text.lower()
    from tests.conftest import desk_headers

    c = client.get("/api/v1/catalog", headers=desk_headers())
    assert c.status_code == 200
    assert "items" in c.json()
    legacy = client.get("/api/health")
    # aiminer app may or may not be importable; prefix must not 404 as empty HTML
    assert legacy.status_code in {200, 404, 401, 403}
    if legacy.status_code == 200:
        assert legacy.headers.get("content-type", "").startswith("application/json") or "ok" in legacy.text.lower() or "error" in legacy.text.lower()


def test_rustminer_rebuild_is_select_only(isolated_home: Path, tmp_path: Path) -> None:
    import sqlite3

    from aiminer.pool_io import persist_alpha_pool_rows

    from finaince.catalog.rebuild import rebuild
    from finaince.catalog.store import FactorCatalog

    db = tmp_path / "rust" / "alpha_miner.db"
    persist_alpha_pool_rows(
        db,
        tmp_path / "rust",
        [
            {
                "id": "alpha_rust1",
                "hypothesis": "from-rust",
                "code": "Rank($close)",
                "perf_metric": 0.06,
                "returns": {f"2024-03-{d:02d}": 0.02 for d in range(1, 13)},
                "metrics": {"information_coefficient": 0.06},
            }
        ],
    )
    # only SELECT: table columns unchanged
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(alpha_pool)")}
    assert "id" in cols and "returns_json" in cols
    import os

    os.environ["FINAINCE_RUSTMINER_DB"] = str(db)
    out = rebuild(source="rustminer")
    assert out["counts"]["rustminer"] >= 1
    recs = [r for r in FactorCatalog().list() if "engine:rustminer" in r.tags]
    assert recs
    assert recs[0].lineage.engine_db == "rustminer"
    # still SELECT-only: no extra columns
    with sqlite3.connect(db) as conn:
        cols2 = {row[1] for row in conn.execute("PRAGMA table_info(alpha_pool)")}
    assert cols2 == cols


def test_job_submit_list_and_cancel(isolated_home: Path) -> None:
    from finaince.jobs.runner import cancel, list_jobs, start_process, submit

    done = submit("evaluate", {"expr": "Rank(close)"}, run=lambda: {"kind": "evaluate", "ok": True})
    assert done["kind"] == "evaluate"
    assert done["status"] == "done"
    assert done.get("pid")
    listed = list_jobs()
    assert any(j["id"] == done["id"] and j["kind"] == "evaluate" for j in listed)

    child = start_process(
        "sleep",
        {},
        [__import__("sys").executable, "-c", "import time; time.sleep(30)"],
    )
    assert child["status"] == "running"
    assert child.get("pid")
    cancelled = cancel(child["id"])
    assert cancelled["ok"] is True
    assert cancelled["job"]["status"] == "cancelled"


def test_eval_cli_returns_numeric_metrics(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from reproagent.settings import get_settings

    fixture = Path(__file__).resolve().parents[2] / "reproagent" / "tests" / "fixtures" / "test_data"
    monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
    get_settings.cache_clear()
    result = runner.invoke(app, ["eval", "Rank(Delta(close, 1))", "--dialect", "repro_polars"])
    assert result.exit_code == 0, result.output
    import json

    body = json.loads(result.stdout)
    assert body["ok"] is True
    metrics = body["metrics"]
    assert any(isinstance(metrics.get(k), (int, float)) for k in ("ic_mean", "sharpe_ratio", "max_drawdown"))


def test_serve_review_route(isolated_home: Path) -> None:
    from fastapi.testclient import TestClient

    from finaince.serve import create_app

    client = TestClient(create_app())
    from tests.conftest import desk_headers

    r = client.get("/api/v1/review", headers=desk_headers())
    assert r.status_code == 200
    assert "items" in r.json()

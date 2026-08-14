"""Live calls: CPA DeepSeek, RiceQuant, ~/Documents/Data, categorized PDF."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from finaince.runtime import (
    DEFAULT_LOCAL_DATA,
    DEFAULT_PDF_ROOT,
    cpa_reachable,
    has_rq_credentials,
    official_deepseek_key,
    resolve_deepseek_llm,
)

PINNED_PDFS = {
    "gf3": (
        DEFAULT_PDF_ROOT
        / "factor_investing"
        / "广发多因子系列3：估值与动量结合的选股模型.pdf"
    ),
    "gf5": (
        DEFAULT_PDF_ROOT
        / "factor_investing"
        / "广发多因子系列5：沪深300成份股的应用分析下.pdf"
    ),
    "ht1": (
        DEFAULT_PDF_ROOT
        / "factor_investing"
        / "海通选股因子系列研究1：弱者终有逆袭日,强势几无持续时：A股市场的动量反转效应研究.pdf"
    ),
}
REAL_PDF = PINNED_PDFS["gf3"]
LIVE_HOME = Path(__file__).resolve().parents[1] / "output" / "live-real"


def _llm():
    return resolve_deepseek_llm(probe=True)


@pytest.mark.live
def test_cpa_deepseek_chat_completion() -> None:
    llm = _llm()
    assert llm["via"] in {"cpa", "deepseek-official"}, llm
    assert llm["api_key"], "DeepSeek/CPA key missing"
    assert llm["model"]
    from openai import OpenAI

    client = OpenAI(api_key=llm["api_key"], base_url=llm["base_url"])
    resp = client.chat.completions.create(
        model=llm["model"],
        messages=[{"role": "user", "content": "Reply with the single word pong"}],
        max_tokens=32,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    assert text, resp.model_dump()
    assert "pong" in text.lower()


@pytest.mark.live
def test_ricequant_get_price() -> None:
    if not has_rq_credentials():
        pytest.skip("RQ_TOKEN / RQ_USER+RQ_PASS not set")
    import rqdatac as rq

    user = (os.getenv("RQ_USER") or "").strip()
    password = (os.getenv("RQ_PASS") or "").strip()
    token = (os.getenv("RQ_TOKEN") or "").strip()
    if user and password:
        rq.init(username=user, password=password)
    elif token:
        rq.init(uri=f"tcp://license:{token}@rqdatad-pro.ricequant.com:16011")
    else:
        pytest.skip("no usable RiceQuant login")
    assert rq.initialized()
    df = rq.get_price(
        "000001.XSHE",
        start_date="2024-01-02",
        end_date="2024-01-10",
        frequency="1d",
        fields=["close"],
    )
    assert df is not None and not df.empty, df


@pytest.mark.live
def test_wiki_upsert_does_not_401_on_deepseek(tmp_path: Path) -> None:
    """Wiki must not send the DeepSeek/CPA chat key to gptsapi embeddings."""
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")
    from aiminer.core.embeddings import resolve_embedding_backend
    from aiminer.core.wiki import LLMWiki

    backend = resolve_embedding_backend("deepseek")
    assert backend["mode"] == "local", backend
    wiki = LLMWiki(
        db_dir=str(tmp_path / "wiki_db"),
        wiki_vault=str(tmp_path / "wiki_vault"),
        embedding_provider="deepseek",
    )
    page_id = wiki.add_or_update_page(
        slug="live_embed_check",
        title="Live embed check",
        content="A real wiki upsert after the DeepSeek embedding 401 fix.",
        metadata={"type": "experiment_card", "status": "active"},
    )
    assert page_id == "wiki_live_embed_check"
    assert (tmp_path / "wiki_vault" / "live_embed_check.md").is_file()
    assert wiki.wiki_col is not None
    assert wiki.wiki_col.count() >= 1


@pytest.mark.live
def test_local_documents_data_panel() -> None:
    parquet = DEFAULT_LOCAL_DATA / "prices.parquet"
    assert parquet.is_file(), parquet
    import polars as pl

    df = pl.read_parquet(parquet)
    assert {"trade_date", "ts_code", "close"} <= set(df.columns)
    assert df.height > 0
    assert df["ts_code"].n_unique() >= 1


@pytest.mark.live
def test_eval_ricequant_momentum() -> None:
    if not has_rq_credentials():
        pytest.skip("RiceQuant credentials missing")
    from finaince.eval.router import EvalRequest, evaluate

    out = evaluate(
        EvalRequest(
            expression="Rank(Delta(close, 1))",
            dialect="repro_polars",
            data_backend="ricequant",
            universe="csi300",
            start="2024-01-02",
            end="2024-03-29",
        )
    )
    assert out.ok is True, out.error
    assert out.metrics.get("rows", 0) > 0
    assert isinstance(out.metrics.get("ic_mean"), (int, float))
    assert out.metrics.get("data_source") == "ricequant"


@pytest.mark.live
def test_reproduce_categorized_pdf_writes_returns(tmp_path: Path) -> None:
    if not REAL_PDF.is_file():
        pytest.skip(f"missing real PDF {REAL_PDF}")
    llm = _llm()
    if not llm["api_key"]:
        pytest.skip("DeepSeek/CPA key missing")
    if not has_rq_credentials():
        pytest.skip("RiceQuant credentials missing")

    from finaince.reproduction import reproduce_report
    from finaince.settings import reproagent_runtime_settings

    home = tmp_path / "live-home"
    home.mkdir()
    os.environ["FINAINCE_HOME"] = str(home)
    os.environ["FINAINCE_DATA_SOURCE"] = "ricequant"
    settings = reproagent_runtime_settings()
    assert settings.allow_mock_llm is False
    assert settings.data_source == "ricequant"
    assert settings.llm_api_key.get_secret_value()

    result = reproduce_report(
        REAL_PDF,
        settings,
        backtest_kwargs={"start_date": "2024-01-02", "end_date": "2024-03-29"},
    )
    assert result is not None
    assert result.get("status") not in {None, ""}
    # Real extraction must not fall back to the canned mock_momentum.
    factors = result.get("factors") or []
    names = {str(f.get("factor_name") or "") for f in factors}
    assert "mock_momentum" not in names, result
    # At least one factor with a formula or a structured no_factors (honest fail).
    if result.get("status") == "passed":
        assert factors
        assert any((f.get("formula") or f.get("metrics")) for f in factors)
        from finaince.catalog.store import FactorCatalog

        recs = FactorCatalog().list(source="reproduction")
        assert recs, "passed reproduce must dual-write catalog"
        assert any(rec.daily_returns for rec in recs), "live ricequant must persist daily_returns"


def _live_reproduce(pdf: Path, tmp_path: Path) -> dict:
    if not pdf.is_file():
        pytest.skip(f"missing pinned PDF {pdf}")
    llm = _llm()
    if not llm["api_key"]:
        pytest.skip("DeepSeek/CPA key missing")
    if not has_rq_credentials():
        pytest.skip("RiceQuant credentials missing")
    from finaince.reproduction import reproduce_report
    from finaince.settings import reproagent_runtime_settings

    home = tmp_path / "live-home"
    home.mkdir()
    os.environ["FINAINCE_HOME"] = str(home)
    os.environ["FINAINCE_DATA_SOURCE"] = "ricequant"
    settings = reproagent_runtime_settings()
    return reproduce_report(
        pdf,
        settings,
        backtest_kwargs={"start_date": "2024-01-02", "end_date": "2024-03-29"},
    ) or {}


@pytest.mark.live
def test_pinned_pdf_gf3_has_returns(tmp_path: Path) -> None:
    result = _live_reproduce(PINNED_PDFS["gf3"], tmp_path)
    assert result.get("status") in {"passed", "partial", "no_factors"}
    names = {str(f.get("factor_name") or "") for f in (result.get("factors") or [])}
    assert "mock_momentum" not in names
    if result.get("status") != "no_factors":
        assert result.get("factors")
        from finaince.catalog.store import FactorCatalog

        recs = FactorCatalog().list(source="reproduction")
        assert any(rec.daily_returns for rec in recs)


@pytest.mark.live
def test_pinned_pdf_gf5_honest_no_mock(tmp_path: Path) -> None:
    result = _live_reproduce(PINNED_PDFS["gf5"], tmp_path)
    names = {str(f.get("factor_name") or "") for f in (result.get("factors") or [])}
    assert "mock_momentum" not in names, result
    assert result.get("status") in {"passed", "partial", "no_factors", "failed"}


@pytest.mark.live
def test_pinned_pdf_ht1_has_returns(tmp_path: Path) -> None:
    result = _live_reproduce(PINNED_PDFS["ht1"], tmp_path)
    names = {str(f.get("factor_name") or "") for f in (result.get("factors") or [])}
    assert "mock_momentum" not in names
    if result.get("status") in {"passed", "partial"}:
        assert result.get("factors")
        from finaince.catalog.store import FactorCatalog

        recs = FactorCatalog().list(source="reproduction")
        assert any(rec.daily_returns for rec in recs)

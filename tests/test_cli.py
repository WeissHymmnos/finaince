"""Unified entry lists and runs discovery + reproduce."""

from __future__ import annotations

from typer.testing import CliRunner

from finaince.cli import app

runner = CliRunner()


def test_sibling_packages_importable() -> None:
    import aiminer
    import reproagent
    from aiminer.core.strategy import selection_score
    from aiminer.manager import cull_alpha_pool
    from reproagent.pipeline import reproduce_report

    assert callable(selection_score)
    assert callable(cull_alpha_pool)
    assert callable(reproduce_report)
    assert aiminer.__name__ == "aiminer"
    assert reproagent.__name__ == "reproagent"


def test_finreportparser_is_finpdfpro_latest() -> None:
    from pathlib import Path

    import pytest
    from reproagent.parser import layout_extractor

    if not hasattr(layout_extractor, "prefer_latest_finpdfpro"):
        pytest.skip("prefer_latest_finpdfpro not on this reproagent")
    from reproagent.parser.layout_extractor import prefer_latest_finpdfpro

    src = prefer_latest_finpdfpro()
    assert src is not None
    import finreportparser

    module = Path(finreportparser.__file__).resolve()
    assert "finpdfpro" in str(module)
    assert str(finreportparser.__version__).startswith("0.5")


def test_layout_extractor_uses_finpdfpro_v05_pipeline(tmp_path) -> None:
    import pytest

    try:
        import finreportparser.output  # noqa: F401
    except ImportError:
        pytest.skip("finreportparser.output not installed")
    from datetime import UTC, datetime
    from pathlib import Path

    from reproagent.models.report import ResearchReport
    from reproagent.parser.layout_extractor import LayoutExtractor
    from reproagent.settings import Settings

    docs = Path(__file__).resolve().parents[2]
    pdf = docs / "finpdfpro" / "tests" / "fixtures" / "broker" / "broker_01.pdf"
    if not pdf.is_file():
        pdf = docs / "reproagent" / "tests" / "fixtures" / "sample_reports" / "minimal.pdf"
    assert pdf.is_file()
    settings = Settings(
        _env_file=None,
        app_env="dev",
        allow_mock_llm=True,
        data_dir=tmp_path / "repro-data",
        parser_backend="finpdfpro",
        finpdfpro_profile="balanced",
        finpdfpro_formula_backend="l1",
    )
    report = ResearchReport(
        id="parse-smoke",
        file_path=pdf,
        file_hash="smoke",
        title=pdf.stem,
        page_count=1,
        validation_status="valid",
        ingested_at=datetime.now(UTC),
    )
    md = LayoutExtractor(settings=settings).extract(report)
    assert isinstance(md, str)
    assert len(md) > 40
    assert "page:" in md or "title" in md.lower() or len(md.splitlines()) >= 3


def test_help_lists_discover_and_reproduce() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    text = result.stdout
    assert "FinAlpha" in text
    assert "discover" in text
    assert "reproduce" in text
    assert "catalog" in text
    assert "eval" in text
    assert "promote" in text or "review" in text
    assert "jobs" in text
    assert "doctor" in text
    assert "serve" in text
    assert "agent" in text
    assert "aiminer" in text.lower() or "swarm" in text.lower() or "discovery" in text.lower()


def test_bare_discover_is_not_a_fake_mine() -> None:
    result = runner.invoke(app, ["discover"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "kept_count" not in combined
    assert "bare discover" in combined or "--demo" in combined


def test_discover_dry_path_scores_and_culls() -> None:
    result = runner.invoke(app, ["discover", "--demo"])
    assert result.exit_code == 0, result.output
    assert "score" in result.stdout
    assert "keep" in result.stdout
    assert "cull" in result.stdout
    assert "kept_count" in result.stdout

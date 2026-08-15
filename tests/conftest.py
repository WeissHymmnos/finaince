from __future__ import annotations

from pathlib import Path

import pytest

from finaince._paths import ensure_import_paths

ensure_import_paths()


@pytest.fixture(autouse=True)
def _offline_unless_live(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic tests stay on mock LLM + local fixture unless marked live."""
    if request.node.get_closest_marker("live"):
        monkeypatch.delenv("ALLOW_MOCK_LLM", raising=False)
        monkeypatch.delenv("FINAINCE_ALLOW_MOCK_LLM", raising=False)
        return
    monkeypatch.setenv("ALLOW_MOCK_LLM", "true")
    from finaince.runtime import packaged_local_panel

    packed = packaged_local_panel()
    sibling = Path(__file__).resolve().parents[2] / "reproagent" / "tests" / "fixtures" / "test_data"
    fixture = packed if packed is not None else sibling
    if fixture is not None and (Path(fixture) / "prices.parquet").is_file():
        monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
        monkeypatch.setenv("FINAINCE_DATA_SOURCE", "local")


REPROAGENT_ROOT = Path(__file__).resolve().parents[2] / "reproagent"
AIMINER_FRONTEND = Path(__file__).resolve().parents[2] / "aiminer" / "frontend"
MINIMAL_PDF = REPROAGENT_ROOT / "tests" / "fixtures" / "sample_reports" / "minimal.pdf"
LOCAL_DATA = REPROAGENT_ROOT / "tests" / "fixtures" / "test_data"


def desk_frontend_pages_present() -> bool:
    pages = AIMINER_FRONTEND / "src" / "pages"
    return all(
        (pages / name).is_file()
        for name in ("CatalogPage.tsx", "ReviewPage.tsx", "ReproducePage.tsx", "AgentPage.tsx")
    )


def aiminer_trace_ui_present() -> bool:
    api = AIMINER_FRONTEND / "src" / "lib" / "api.ts"
    return api.is_file() and "listTrace" in api.read_text()


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "finaince-home"
    home.mkdir()
    monkeypatch.setenv("FINAINCE_HOME", str(home))
    monkeypatch.delenv("FINAINCE_CATALOG", raising=False)
    return home


@pytest.fixture
def sample_report_path() -> Path:
    assert MINIMAL_PDF.is_file(), f"missing fixture {MINIMAL_PDF}"
    return MINIMAL_PDF


@pytest.fixture
def offline_settings(tmp_path: Path):
    from reproagent.settings import Settings

    return Settings(
        _env_file=None,
        app_env="dev",
        allow_mock_llm=True,
        allow_formula_fallback=True,
        llm_api_key="",
        parser_backend="finpdfpro",
        data_source="local",
        local_data_path=LOCAL_DATA,
        data_dir=tmp_path / "reproagent-data",
    )

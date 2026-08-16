"""Human CLI + workbench session: shipped entry points only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from finaince.catalog.hooks import accept_library_entry
from finaince.catalog.store import FactorCatalog
from finaince.serve import create_app
from tests.conftest import AIMINER_FRONTEND, MINIMAL_PDF, desk_headers

ROOT = Path(__file__).resolve().parents[1]


def _cli(home: Path, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
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


def _json_out(text: str) -> dict:
    blob = text.strip()
    if not blob.startswith("{"):
        start = blob.find("{")
        blob = blob[start:]
    return json.loads(blob)


def test_human_cli_session_is_parseable(isolated_home: Path) -> None:
    help_r = _cli(isolated_home, ["--help"])
    assert help_r.returncode == 0, help_r.stderr
    help_text = help_r.stdout
    assert "FinAlpha" in help_text
    for name in ("discover", "reproduce", "catalog", "eval", "review", "serve", "agent"):
        assert name in help_text, name

    doctor = _cli(isolated_home, ["doctor"])
    doc = _json_out(doctor.stdout)
    assert doc["product_name"] == "FinAlpha"
    assert isinstance(doc["ok"], bool)
    if not doc["ok"]:
        assert doctor.returncode != 0
    else:
        assert doctor.returncode == 0

    first = _json_out(_cli(isolated_home, ["baseline"]).stdout)
    second = _json_out(_cli(isolated_home, ["baseline"]).stdout)
    assert first["ok"] == second["ok"]
    assert first["metrics"].get("ic_mean") == second["metrics"].get("ic_mean")
    assert first["metrics"].get("sharpe_ratio") == second["metrics"].get("sharpe_ratio")
    claim = str(first.get("claim") or "")
    assert "local_panel" in claim
    assert "0 bps" in claim
    assert "smoke" in claim.lower()

    ev = _cli(
        isolated_home,
        ["eval", "Rank(Delta(close, 1))", "--dialect", "repro_polars", "--backend", "local"],
    )
    assert ev.returncode == 0, ev.stderr + ev.stdout
    ev_body = _json_out(ev.stdout)
    assert isinstance(ev_body.get("ok"), bool)
    assert isinstance(ev_body.get("metrics"), dict)

    cat = _cli(isolated_home, ["catalog"])
    assert cat.returncode == 0, cat.stderr
    cat_body = _json_out(cat.stdout)
    assert "items" in cat_body


def test_human_workbench_reads_are_html_and_json(isolated_home: Path) -> None:
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    for route in ("/", "/review", "/reproduce", "/agent"):
        page = client.get(route)
        assert page.status_code == 200, route
        text = page.text
        assert "FinAlpha" in text
        assert 'id="root"' in text
        assert "spa disabled" not in text.lower()
        assert "frontend not built" not in text.lower()
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert "json" in health.headers.get("content-type", "")
    assert health.json()["product"] == "FinAlpha"
    catalog = client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    assert "json" in catalog.headers.get("content-type", "")
    assert "items" in catalog.json()
    review = client.get("/api/v1/review")
    assert review.status_code == 200
    assert "json" in review.headers.get("content-type", "")
    assert "items" in review.json()


def test_human_mutation_loop_on_served_app(isolated_home: Path, sample_report_path: Path) -> None:
    from aiminer.pool_io import load_alpha_pool_rows
    from finaince.settings import get_settings
    import finaince.serve as serve_mod

    class _F:
        name = "human_mom"
        name_cn = "human"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-human"

    class _E:
        id = "lib-human-mom"
        report_id = "rep-human-mom"
        factor = _F()

    returns = {f"2024-05-{d:02d}": 0.01 for d in range(1, 13)}
    accept_library_entry(
        _E(),
        extras={"metrics": {"ic_mean": 0.06}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "human_mom")
    serve_mod.app = None
    client = TestClient(create_app())
    bare = client.post("/api/v1/promote", json={"catalog_id": rec.id, "direction": "to_pool"})
    assert bare.status_code in {401, 403}
    posted = client.post(
        "/api/v1/promote",
        json={"catalog_id": rec.id, "direction": "to_pool"},
        headers=desk_headers(),
    )
    assert posted.status_code == 200
    promo = posted.json()
    assert promo["ok"] is True
    queue = client.get("/api/v1/review").json()
    assert any(item["id"] == promo["promotion_id"] for item in queue["items"])
    rejected = client.post(
        f"/api/v1/review/{promo['promotion_id']}/reject",
        headers=desk_headers(),
    )
    assert rejected.status_code == 200
    again = client.post(
        "/api/v1/promote",
        json={"catalog_id": rec.id, "direction": "to_pool"},
        headers=desk_headers(),
    )
    assert again.status_code == 200
    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    blocked = client.post(
        f"/api/v1/review/{again.json()['promotion_id']}/approve",
        json={"override": ["thin_panel"]},
        headers=desk_headers(),
    )
    assert blocked.status_code == 403
    assert list(load_alpha_pool_rows(get_settings().aiminer_db)) == before
    qlib = client.post(
        "/api/v1/eval",
        json={"expression": "Rank($close)", "dialect": "qlib"},
        headers=desk_headers(),
    )
    assert qlib.status_code == 200
    assert qlib.json()["ok"] is False
    job = client.post(
        "/api/v1/reproduce",
        json={"pdf_path": str(sample_report_path), "sync": False},
        headers=desk_headers(),
    )
    assert job.status_code == 200, job.text
    job_id = job.json().get("id")
    assert job_id
    polled = client.get(f"/api/v1/jobs/{job_id}")
    assert polled.status_code == 200
    assert polled.json()["id"] == job_id


def test_human_pages_surface_http_errors_and_stay_same_origin() -> None:
    pages = AIMINER_FRONTEND / "src" / "pages"
    catalog = (pages / "CatalogPage.tsx").read_text()
    review = (pages / "ReviewPage.tsx").read_text()
    repro = (pages / "ReproducePage.tsx").read_text()
    agent = (pages / "AgentPage.tsx").read_text()
    layout = (AIMINER_FRONTEND / "src" / "components" / "Layout.tsx").read_text()
    app = (AIMINER_FRONTEND / "src" / "App.tsx").read_text()
    assert (pages / "CatalogPage.tsx").is_file()
    assert (pages / "ReviewPage.tsx").is_file()
    assert (pages / "ReproducePage.tsx").is_file()
    assert (pages / "AgentPage.tsx").is_file()
    assert 'path="/" element={<CatalogPage />}' in app
    assert 'path="/review"' in app
    assert 'path="/reproduce"' in app
    assert 'path="/agent"' in app
    assert "/api/v1/catalog" in catalog
    assert "!r.ok" in catalog or "r.ok" in catalog
    assert "override" in review and "thin_panel" in review
    assert 'json.stringify({ override' not in review.lower()
    assert "/api/v1/reproduce" in repro
    assert "FINAINCE_PDF_ROOT" in repro
    assert "/api/v1/agent" in agent
    assert "FINAINCE_DESK_TOKEN" in layout
    assert "localhost:8000" not in catalog
    assert "localhost:8000" not in review
    from finaince.serve import resolve_workbench_dist

    dist = resolve_workbench_dist()
    assert dist is not None
    chunk = next((dist / "assets").glob("CatalogPage-*.js"), None)
    assert chunk is not None
    built = chunk.read_text(encoding="utf-8")
    assert "/api/v1/" in built
    assert "localhost:8000" not in built

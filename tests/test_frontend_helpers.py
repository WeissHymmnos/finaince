"""Call shipped frontend auth helpers and assert desk source contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "aiminer" / "frontend"


def test_uvicorn_websocket_library_is_installed() -> None:
    import websockets

    assert websockets.__version__


def test_build_auth_headers_from_shipped_module() -> None:
    script = FRONTEND / "src" / "lib" / "authHeaders.js"
    assert script.is_file()
    proc = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import { buildAuthHeaders } from './src/lib/authHeaders.js';"
            "console.log(JSON.stringify({"
            "empty: buildAuthHeaders(''),"
            "blank: buildAuthHeaders('   '),"
            "key: buildAuthHeaders('secret'),"
            "bearer: buildAuthHeaders('Bearer abc')"
            "}));",
        ],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["empty"] == {}
    assert body["blank"] == {}
    assert body["key"]["Authorization"] == "Bearer secret"
    assert body["key"]["X-API-Key"] == "secret"
    assert body["bearer"]["Authorization"] == "Bearer abc"
    assert body["bearer"]["X-API-Key"] == "abc"


def test_frontend_routes_controls_and_non_ai_stylesheet() -> None:
    assert (FRONTEND / "src" / "pages" / "CatalogPage.tsx").is_file()
    src = FRONTEND / "src"
    app = (src / "App.tsx").read_text()
    styles = (src / "styles.css").read_text()
    catalog = (src / "pages" / "CatalogPage.tsx").read_text()
    review = (src / "pages" / "ReviewPage.tsx").read_text()
    repro = (src / "pages" / "ReproducePage.tsx").read_text()
    agent = (src / "pages" / "AgentPage.tsx").read_text()
    for route in (
        'path="/"',
        'path="/catalog/:id"',
        'path="/review"',
        'path="/reproduce"',
        'path="/agent"',
        'path="/runs"',
        'path="/runs/:runId"',
        'path="/pool"',
        'path="/manual"',
        'path="/strategy"',
        'path="/wiki"',
        'path="/ops"',
    ):
        assert route in app, route
    assert "/api/v1/promote" in catalog and "deskFetch" in catalog
    assert "reject" in review and "gates" in review and "override" in review
    assert "deskFetch" in review
    assert "/api/v1/jobs/" in repro and "no_factors" in repro and "deskFetch" in repro
    assert "ok === false" in agent and "deskFetch" in agent
    assert "backdrop-filter" not in styles.lower()
    assert "linear-gradient" not in styles
    font_line = next(line for line in styles.splitlines() if "font-family" in line)
    assert "Inter" not in font_line and "Geist" not in font_line
    assert "IBM Plex Sans" in font_line


def test_workbench_aiminer_routes_do_not_404(isolated_home: Path) -> None:
    from fastapi.testclient import TestClient

    from finaince.catalog.hooks import accept_pool_row
    from finaince.serve import create_app
    import finaince.serve as serve_mod
    from tests.conftest import DESK_TOKEN, desk_headers

    accept_pool_row(
        {
            "id": "alpha_ui1",
            "hypothesis": "ui factor",
            "code": "Rank($close)",
            "ic": 0.04,
            "returns": {f"2024-01-{d:02d}": 0.01 for d in range(1, 8)},
        }
    )
    serve_mod.app = None
    client = TestClient(create_app())
    auth = desk_headers()
    results = client.get("/api/results", headers=auth)
    assert results.status_code == 200
    body = results.json()
    assert "items" in body and "total" in body
    assert any(item.get("hypothesis") == "ui factor" for item in body["items"])
    wiki = client.get("/api/wiki/index", headers=auth)
    assert wiki.status_code == 200
    assert "items" in wiki.json()
    graph = client.get("/api/wiki/graph", headers=auth)
    assert graph.status_code == 200
    assert "nodes" in graph.json()
    runs = client.get("/api/swarm/runs", headers=auth)
    assert runs.status_code == 200
    assert "items" in runs.json()
    status = client.get("/api/swarm/status", headers=auth)
    assert status.status_code == 200
    assert "running_count" in status.json()
    with client.websocket_connect(f"/ws?token={DESK_TOKEN}") as socket:
        hello = socket.receive_json()
        assert hello.get("event") == "connected"
        socket.send_text("ping")
        assert socket.receive_text() == "pong"

    missing = []
    for method, path in (
        ("DELETE", "/api/swarm/runs/missing"),
        ("POST", "/api/backtest/validate"),
        ("POST", "/api/backtest/run"),
        ("GET", "/api/backtest/history"),
        ("GET", "/api/backtest/missing"),
        ("DELETE", "/api/backtest/missing"),
        ("GET", "/api/charts/missing"),
        ("GET", "/api/strategies"),
        ("GET", "/api/strategies/missing"),
        ("POST", "/api/strategy/run"),
        ("GET", "/api/strategy/history"),
        ("DELETE", "/api/strategy/missing"),
        ("PUT", "/api/wiki/page/missing"),
        ("POST", "/api/admin/reset"),
        ("GET", "/api/readiness"),
    ):
        response = client.request(
            method, path, json={} if method in {"POST", "PUT"} else None, headers=auth
        )
        if response.status_code == 404 and "Not Found" == response.json().get("detail"):
            missing.append(f"{method} {path}")
    assert not missing, missing


def test_workbench_mutations_round_trip(isolated_home: Path) -> None:
    import os

    from fastapi.testclient import TestClient

    from finaince.serve import create_app
    import finaince.serve as serve_mod
    from tests.conftest import desk_headers

    vault = isolated_home / "aiminer" / "data" / "wiki_vault"
    vault.mkdir(parents=True)
    (vault / "desk_note.md").write_text(
        "---\ntitle: Desk note\ntype: technical_ref\nstatus: active\n---\nhello\n",
        encoding="utf-8",
    )
    serve_mod.app = None
    client = TestClient(create_app())
    auth = desk_headers()

    health = client.get("/api/health")
    if not health.json().get("degraded"):
        return

    validated = client.post(
        "/api/backtest/validate", json={"expression": "Rank($close)"}, headers=auth
    )
    assert validated.status_code == 200
    assert validated.json()["ok"] is True

    ran = client.post(
        "/api/backtest/run",
        json={
            "expression": "Rank($close)",
            "start_date": "2023-01-03",
            "end_date": "2023-02-10",
            "engine": "polars",
            "data_backend": "local",
            "market": "local_panel",
            "label": "fallback-bt",
        },
        headers=auth,
    )
    assert ran.status_code == 200, ran.text
    job_id = ran.json()["job_id"]
    assert ran.json()["metrics"]
    got = client.get(f"/api/backtest/{job_id}", headers=auth)
    assert got.status_code == 200
    history = client.get("/api/backtest/history", headers=auth)
    assert history.status_code == 200
    assert any(item.get("job_id") == job_id for item in history.json())

    strategy = client.post(
        "/api/strategy/run",
        json={
            "expression": "Rank($close)",
            "data_backend": "local",
            "strategy_config": {"label": "fallback-st", "strategy_mode": "cross_sectional"},
        },
        headers=auth,
    )
    assert strategy.status_code == 200, strategy.text
    strategy_id = strategy.json()["strategy_id"]
    assert client.get(f"/api/strategies/{strategy_id}", headers=auth).status_code == 200
    assert any(
        item.get("strategy_id") == strategy_id
        for item in client.get("/api/strategy/history", headers=auth).json()
    )

    saved = client.put(
        "/api/wiki/page/desk_note",
        json={"content": "---\ntitle: Desk note\n---\nupdated\n"},
        headers=auth,
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    page = client.get("/api/wiki/page/desk_note", headers=auth)
    assert page.status_code == 200
    assert "updated" in page.text

    preview = client.post("/api/admin/reset", json={"scopes": ["runs"], "confirm": False}, headers=auth)
    assert preview.status_code == 200
    assert preview.json()["confirm"] is False
    blocked = client.post(
        "/api/admin/reset",
        json={"scopes": ["runs"], "confirm": True, "reset_token": "nope"},
        headers=auth,
    )
    assert blocked.status_code in {403, 503}

    assert client.delete(f"/api/backtest/{job_id}", headers=auth).json()["status"] == "deleted"
    assert client.delete(f"/api/strategy/{strategy_id}", headers=auth).json()["status"] == "deleted"
    assert client.get(f"/api/backtest/{job_id}", headers=auth).status_code == 404
    assert client.delete("/api/swarm/runs/missing", headers=auth).status_code == 404

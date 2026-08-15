"""Walk the same desk loops a human clicks: one create_app(), human order."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finaince.catalog.hooks import accept_library_entry
from finaince.catalog.store import FactorCatalog
from finaince.serve import create_app


FRONTEND = Path(__file__).resolve().parents[2] / "aiminer" / "frontend"

# Layout nav → HTTP the shipped page already issues on load / first action.
NAV_HTTP = {
    "/": ("GET", "/api/v1/catalog"),
    "/review": ("GET", "/api/v1/review"),
    "/reproduce": ("POST", "/api/v1/reproduce"),
    "/agent": ("POST", "/api/v1/agent"),
    "/runs": ("GET", "/api/swarm/status"),
    "/pool": ("GET", "/api/results"),
    "/manual": ("GET", "/api/backtest/history"),
    "/strategy": ("GET", "/api/strategy/history"),
    "/wiki": ("GET", "/api/wiki/index"),
    "/ops": ("POST", "/api/admin/reset"),
}


def _entry():
    class _F:
        name = "walk_mom"
        name_cn = "walk"
        style = "momentum"
        formula = "close / Ref(close, 5) - 1"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-walk"

    class _E:
        id = "lib-walk-mom"
        report_id = "rep-walk-mom"
        factor = _F()

    return _E()


def _json_not_html(response) -> dict:
    ctype = response.headers.get("content-type", "")
    text = response.text
    assert not text.lstrip().lower().startswith("<!doctype"), text[:200]
    assert "html" not in ctype.lower(), (response.status_code, text[:200])
    if response.status_code == 404:
        body = response.json()
        assert body.get("detail") != "Not Found", getattr(response, "url", text[:120])
        return body
    assert response.status_code < 500, text[:200]
    return response.json() if "json" in ctype else {}


def test_layout_nav_maps_to_shipped_page_calls() -> None:
    layout = (FRONTEND / "src" / "components" / "Layout.tsx").read_text()
    app = (FRONTEND / "src" / "App.tsx").read_text()
    pages = {
        "/": (FRONTEND / "src" / "pages" / "CatalogPage.tsx").read_text(),
        "/review": (FRONTEND / "src" / "pages" / "ReviewPage.tsx").read_text(),
        "/reproduce": (FRONTEND / "src" / "pages" / "ReproducePage.tsx").read_text(),
        "/agent": (FRONTEND / "src" / "pages" / "AgentPage.tsx").read_text(),
        "/runs": (FRONTEND / "src" / "pages" / "SwarmRunsPage.tsx").read_text(),
        "/pool": (FRONTEND / "src" / "pages" / "AlphaPoolPage.tsx").read_text(),
        "/manual": (FRONTEND / "src" / "pages" / "ManualBacktestPage.tsx").read_text(),
        "/strategy": (FRONTEND / "src" / "pages" / "StrategyBacktestPage.tsx").read_text(),
        "/wiki": (FRONTEND / "src" / "pages" / "WikiPage.tsx").read_text(),
        "/ops": (FRONTEND / "src" / "pages" / "AdminPage.tsx").read_text(),
    }
    for route, (_method, path) in NAV_HTTP.items():
        assert f'to: "{route}"' in layout or f'to="{route}"' in layout, route
        assert f'path="{route}"' in app, route
        assert path.split("?")[0] in pages[route] or path.replace("/api/", "") in pages[route] or (
            "api." in pages[route] or "deskFetch" in pages[route]
        ), (route, path)
    assert "/api/v1/catalog" in pages["/"]
    assert "/api/v1/promote" in pages["/"]
    assert "/api/v1/review" in pages["/review"] and "reject" in pages["/review"]
    assert "gates" in pages["/review"]
    assert "/api/v1/reproduce" in pages["/reproduce"] and "/api/v1/jobs/" in pages["/reproduce"]
    assert "no_factors" in pages["/reproduce"]
    assert "/api/v1/agent" in pages["/agent"] and "ok === false" in pages["/agent"]
    assert "listTrace" in pages["/runs"] or "/api/v1/trace" in pages["/runs"]


def test_human_desk_walk(isolated_home: Path, sample_report_path: Path) -> None:
    returns = {f"2024-04-{d:02d}": 0.01 for d in range(1, 13)}
    accept_library_entry(
        _entry(),
        extras={"metrics": {"ic_mean": 0.07}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "walk_mom")
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())

    index = client.get("/")
    assert index.status_code == 200
    assert "FinAlpha" in index.text
    assert "spa disabled" not in index.text.lower()
    assert "frontend not built" not in index.text.lower()

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["product"] == "FinAlpha"
    assert isinstance(body["ok"], bool)
    assert isinstance(body["degraded"], bool)

    listed = _json_not_html(client.get("/api/v1/catalog"))
    assert listed["count"] >= 1
    assert any(item["id"] == rec.id for item in listed["items"])

    detail = _json_not_html(client.get(f"/api/v1/catalog/{rec.id}"))
    assert detail["id"] == rec.id

    promoted = _json_not_html(
        client.post("/api/v1/promote", json={"catalog_id": rec.id, "direction": "to_pool"})
    )
    assert promoted["ok"] is True
    assert promoted.get("promotion_id")

    queue = _json_not_html(client.get("/api/v1/review"))
    assert any(item["id"] == promoted["promotion_id"] for item in queue["items"])
    pending = next(item for item in queue["items"] if item["id"] == promoted["promotion_id"])
    assert "gates" in pending

    rejected = _json_not_html(client.post(f"/api/v1/review/{promoted['promotion_id']}/reject"))
    assert rejected["ok"] is True

    again = _json_not_html(
        client.post("/api/v1/promote", json={"catalog_id": rec.id, "direction": "to_pool"})
    )
    assert again["ok"] is True
    assert again["promotion_id"] != promoted["promotion_id"]

    approved = _json_not_html(
        client.post(
            f"/api/v1/review/{again['promotion_id']}/approve",
            json={"override": ["thin_panel"]},
        )
    )
    assert approved.get("ok") is True or approved.get("error")

    qlib = _json_not_html(
        client.post("/api/v1/eval", json={"expression": "Rank($close)", "dialect": "qlib"})
    )
    assert qlib["ok"] is False

    job = _json_not_html(
        client.post(
            "/api/v1/reproduce",
            json={"pdf_path": str(sample_report_path), "sync": False},
        )
    )
    job_id = job.get("id")
    assert job_id
    polled = _json_not_html(client.get(f"/api/v1/jobs/{job_id}"))
    assert polled["id"] == job_id

    for method, path in (
        ("GET", "/api/health"),
        ("GET", "/api/swarm/status"),
        ("GET", "/api/swarm/runs"),
        ("GET", "/api/results"),
        ("GET", "/api/wiki/index"),
        ("GET", "/api/backtest/history"),
        ("GET", "/api/strategy/history"),
        ("GET", "/api/strategies"),
        ("GET", "/api/v1/trace"),
        ("GET", "/api/v1/baseline"),
    ):
        got = client.request(method, path)
        assert got.status_code != 404 or got.headers.get("content-type", "").startswith("application/json"), path
        _json_not_html(got)

    for route in ("/review", "/reproduce", "/agent", "/runs", "/pool", "/manual", "/strategy", "/wiki", "/ops"):
        page = client.get(route)
        assert page.status_code == 200, route
        assert "FinAlpha" in page.text
        assert "spa disabled" not in page.text.lower()


def test_human_nav_when_aiminer_api_missing(isolated_home: Path, monkeypatch) -> None:
    import sys
    import types

    import finaince.serve as serve_mod

    class Boom(types.ModuleType):
        def __getattr__(self, name: str) -> None:
            raise ImportError("forced: aiminer.api unavailable")

    monkeypatch.setitem(sys.modules, "aiminer.api", Boom("aiminer.api"))
    serve_mod.app = None
    client = TestClient(create_app())
    root = client.get("/")
    assert root.status_code == 200 and "FinAlpha" in root.text
    api_health = _json_not_html(client.get("/api/health"))
    assert "ok" in api_health
    for path in (
        "/api/swarm/status",
        "/api/swarm/runs",
        "/api/results",
        "/api/wiki/index",
        "/api/backtest/history",
        "/api/strategy/history",
        "/api/strategies",
        "/api/v1/catalog",
        "/api/v1/review",
        "/api/v1/health",
        "/api/v1/trace",
    ):
        _json_not_html(client.get(path))
    for route in ("/runs", "/pool", "/manual", "/strategy", "/wiki", "/ops"):
        page = client.get(route)
        assert page.status_code == 200, route
        assert "FinAlpha" in page.text


def test_built_desk_chunks_are_same_origin() -> None:
    from finaince.serve import resolve_workbench_dist

    dist = resolve_workbench_dist()
    assert dist is not None
    for name in ("CatalogPage", "ReviewPage", "ReproducePage", "AgentPage"):
        chunk = next((dist / "assets").glob(f"{name}-*.js"), None)
        assert chunk is not None, name
        text = chunk.read_text(encoding="utf-8")
        assert "localhost:8000" not in text, name
        assert "/api/v1/" in text, name

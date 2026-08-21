"""WS-F workbench tests: gates endpoint + stub SPA contract strings."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.conftest import desk_headers


def _seed_promotion(isolated_home):
    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
    from finaince.review.desk import promote

    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_wsF",
        name="wsf",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        universe="local_panel",
        metrics=FactorMetrics(ic=0.05, extra={"ic_ir": 0.4}),
        daily_returns={f"2024-01-{d:02d}": d / 300.0 for d in range(1, 29)},
        lineage=FactorLineage(source="manual", source_ref="wsf"),
        created_at=now,
        updated_at=now,
    )
    FactorCatalog().upsert(rec)
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    return rec.id, promo["promotion_id"]


def test_gates_endpoint_read_only(isolated_home):
    from finaince.serve import create_app

    catalog_id, promotion_id = _seed_promotion(isolated_home)
    client = TestClient(create_app())
    res = client.get(f"/api/v1/review/{promotion_id}/gates", headers=desk_headers())
    assert res.status_code == 200
    payload = res.json()
    assert payload["promotion_id"] == promotion_id
    assert payload["catalog_id"] == catalog_id
    assert payload["read_only"] is True
    assert isinstance(payload["failures"], list)
    assert "details" in payload


def test_gates_endpoint_auth_and_404(isolated_home):
    from finaince.serve import create_app

    client = TestClient(create_app())
    unauth = client.get("/api/v1/review/nonexistent/gates", headers={"X-API-Key": "wrong"})
    assert unauth.status_code in (401, 403)
    authed = client.get("/api/v1/review/does-not-exist/gates", headers=desk_headers())
    assert authed.status_code == 404


def test_stub_spa_contract_strings():
    from pathlib import Path

    from finaince.serve import resolve_workbench_dist

    html_path = Path(__file__).resolve().parents[1] / "src" / "finaince" / "web" / "index.html"
    assert html_path.is_file()
    content = html_path.read_text()
    for needle in ("pane-catalog", "pane-review", "pane-trace", "/gates`", "adversary"):
        assert needle in content
    with_env = resolve_workbench_dist()
    assert with_env is None or (with_env / "index.html").is_file()

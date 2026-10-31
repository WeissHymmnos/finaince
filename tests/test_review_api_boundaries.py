"""Exercise public HTTP validation and promotion lifecycle boundaries."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from finaince.catalog.store import FactorCatalog
from finaince.domain.factor import FactorExpression, FactorLineage, FactorRecord
from finaince.review import desk
from finaince.serve import DeskAuthGate, create_app
from tests.conftest import desk_headers


@pytest.fixture
def client(isolated_home, monkeypatch):
    from finaince import settings

    monkeypatch.setattr(settings, "doctor_report", lambda: {"ok": True})
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("cost", ["mistyped", "NaN", "Infinity", -5, None, True, []])
def test_invalid_cost_is_rejected_before_evaluation(client, monkeypatch, cost):
    from finaince.eval import router

    def unexpected(req):
        pytest.fail("invalid costs must not reach an evaluation engine")

    monkeypatch.setattr(router, "evaluate", unexpected)
    response = client.post("/api/v1/eval", json={"expression": "Rank(close)", "cost_bps": cost},
                           headers=desk_headers())
    assert response.status_code == 400


@pytest.mark.parametrize("path,key", [("agent", "max_turns"), ("loop", "steps"), ("loop", "workers")])
@pytest.mark.parametrize("value", ["invalid", 1.5, 0, -1, None, True])
def test_invalid_job_counts_are_client_errors(client, path, key, value):
    response = client.post(f"/api/v1/{path}", json={"prompt": "test", key: value}, headers=desk_headers())
    assert response.status_code == 400


def test_string_false_is_not_treated_as_synchronous_execution(client):
    response = client.post("/api/v1/loop", json={"sync": "false"}, headers=desk_headers())
    assert response.status_code == 400


def test_websocket_namespace_requires_desk_token():
    app = FastAPI()
    app.add_middleware(DeskAuthGate)

    @app.websocket("/api/v1/events")
    async def events(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"ok": True})
        await socket.close()

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/api/v1/events"):
                pytest.fail("unauthenticated websocket was accepted")
        assert caught.value.code == 1008
        with client.websocket_connect("/api/v1/events", headers=desk_headers()) as socket:
            assert socket.receive_json() == {"ok": True}


def _record():
    now = datetime.now(UTC)
    return FactorRecord(
        id="review-boundary", name="boundary",
        expression=FactorExpression(dialect="repro_polars", text="Rank(close)"),
        lineage=FactorLineage(source="manual", source_ref="review-boundary"),
        created_at=now, updated_at=now,
    )


def test_invalid_promotion_direction_never_mutates_catalog(isolated_home):
    cat = FactorCatalog()
    record = cat.upsert(_record())
    assert desk.promote(record.id, direction="unknown")["ok"] is False
    assert cat.get(record.id).status == "candidate"
    assert not cat.list_promotions(None)
    pid = cat.add_promotion(record.id, "unknown", "pending", {})
    assert desk.approve(pid, adversary=False)["error"] == "invalid_direction"
    assert cat.get(record.id).status == "candidate"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_reject_cannot_rewrite_terminal_promotion(isolated_home, decision):
    cat = FactorCatalog()
    record = _record()
    record.status = "ready"
    cat.upsert(record)
    pid = cat.add_promotion(record.id, "to_pool", decision, {})
    assert desk.reject(pid)["ok"] is False
    assert cat.list_promotions(None)[0]["decision"] == decision
    assert cat.get(record.id).status == "ready"
    assert desk.reject("unknown")["ok"] is False


@pytest.mark.parametrize("verdict", [{"ok": False, "verdict": "approved"}, {"ok": True}, {}])
def test_adversary_must_explicitly_approve(isolated_home, monkeypatch, verdict):
    from finaince.review import adversary

    cat = FactorCatalog()
    record = cat.upsert(_record())
    pid = cat.add_promotion(record.id, "to_library", "pending", {})
    monkeypatch.setattr(desk, "evaluate_gates", lambda *a, **kw: {"passed": True})
    monkeypatch.setattr(adversary, "adversarial_review", lambda *a: verdict)
    result = desk.approve(pid)
    assert result["ok"] is False
    assert result["error"] == "adversary_rejected"
    assert cat.list_promotions()[0]["decision"] == "pending"


@pytest.mark.parametrize("bad_id", ["..%2Fsecret", "..", "a..b", "bad$id"])
def test_aiminer_fallback_rejects_path_traversal_identifiers(isolated_home, bad_id):
    from fastapi import FastAPI

    from finaince.aiminer_fallback import register_aiminer_fallbacks

    fallback_app = FastAPI()
    register_aiminer_fallbacks(fallback_app, reason="test")
    with TestClient(fallback_app) as fb_client:
        assert fb_client.get(f"/api/backtest/{bad_id}").status_code in (400, 404)
        assert fb_client.delete(f"/api/backtest/{bad_id}").status_code in (400, 404)
        assert fb_client.get(f"/api/strategies/{bad_id}").status_code in (400, 404)
        assert fb_client.delete(f"/api/strategy/{bad_id}").status_code in (400, 404)
        assert fb_client.get(f"/api/reports/{bad_id}").status_code in (400, 404)

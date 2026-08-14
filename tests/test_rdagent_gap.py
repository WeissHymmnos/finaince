"""Drive shipped trace / isolate / loop / impl_status / doctor gap closures."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finaince.eval.router import EvalRequest, evaluate
from finaince.impl_status import annotate_reproduce, classify_research_outcome
from finaince.isolate import child_isolate, run_isolated, upsert_isolated
from finaince.loop import choose_next_action, run_loop
from finaince.review.gates import evaluate_gates
from finaince.serve import create_app
from finaince.trace import list_chain


GOOD_SRC = """
NAME = "iso_mom"
def compute(panel):
    close = list(panel["close"])
    out = [0.0]
    for i in range(1, len(close)):
        out.append((close[i] - close[i - 1]) / close[i - 1])
    return out
"""


def test_trace_two_actions_cite_first_id(isolated_home: Path) -> None:
    first = evaluate(EvalRequest(expression="Rank(Delta(close, 1))", dialect="repro_polars"))
    assert first.dialect == "repro_polars"
    second = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert second.ok is False
    chain = list_chain(limit=10)
    assert len(chain) >= 2
    later, earlier = chain[0], chain[1]
    assert later["cites"] == earlier["id"] or later["parent_id"] == earlier["id"]
    assert later["id"] != earlier["id"]
    assert later.get("summary") or later.get("error") or later.get("metrics")


def test_http_trace_after_two_desk_actions(isolated_home: Path) -> None:
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    ev1 = client.post("/api/v1/eval", json={"expression": "Rank(Delta(close, 1))", "dialect": "repro_polars"})
    assert ev1.status_code == 200
    ev2 = client.post("/api/v1/eval", json={"expression": "Rank($close)", "dialect": "qlib"})
    assert ev2.status_code == 200
    assert ev2.json()["ok"] is False
    listed = client.get("/api/v1/trace")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) >= 2
    assert items[0]["cites"] == items[1]["id"] or items[0]["parent_id"] == items[1]["id"]


def test_isolated_impl_fail_then_success_and_gates(isolated_home: Path) -> None:
    bad = child_isolate({"source": "import socket\n"})
    assert bad.get("ok") is False
    pip = child_isolate({"source": "x = 'pip install evil'\n"})
    assert pip.get("ok") is False
    child = child_isolate({"source": GOOD_SRC})
    assert child.get("ok") is True, child
    assert child.get("daily_returns")
    stored = upsert_isolated(child, universe="csi300")
    assert stored.get("ok") is True
    cid = stored["catalog_id"]
    from finaince.catalog.store import FactorCatalog

    rec = FactorCatalog().get(cid)
    assert rec is not None
    gates = evaluate_gates(rec, direction="to_pool")
    assert "thin_panel" in gates["failures"]
    rec.universe = "local_panel"
    FactorCatalog().upsert(rec)
    local = evaluate_gates(FactorCatalog().get(cid), direction="to_pool")
    assert "thin_panel" not in local["failures"]
    via = run_isolated(GOOD_SRC, name="iso_via_child")
    assert via.get("ok") is True or via.get("skipped") is True
    if via.get("skipped"):
        assert via.get("error")


def test_empty_extract_stays_no_factors_described_needs_impl() -> None:
    empty = classify_research_outcome(factor_count=0, described=False, status="no_factors")
    assert empty == "no_factors"
    stamped = annotate_reproduce({"status": "no_factors", "factors": [], "factor_count": 0})
    assert stamped["impl_status"] == "no_factors"
    assert stamped["status"] == "no_factors"
    needs = classify_research_outcome(expression="MyCustomAlpha(close)")
    assert needs == "needs_impl"
    described = annotate_reproduce(
        {
            "status": "no_factors",
            "factor_count": 0,
            "factors": [{"name": "custom", "formula": "MyCustomAlpha(close)", "description": "hand wavy"}],
        }
    )
    assert described["impl_status"] == "needs_impl"
    runnable = classify_research_outcome(expression="Rank(Delta(close, 1))")
    assert runnable == "runnable"


def test_loop_alternates_factor_and_model(isolated_home: Path) -> None:
    assert choose_next_action([]) == "factor"
    assert choose_next_action([{"action": "factor"}]) == "model"
    out = run_loop(steps=2)
    assert "factor" in out["actions"]
    assert "model" in out["actions"]
    assert out.get("factor_set") is not None
    assert out.get("model_config") is not None
    if not out.get("equity_curve"):
        assert out.get("skip_reason") or out.get("degraded")


def test_http_loop_and_impl(isolated_home: Path) -> None:
    import finaince.serve as serve_mod

    serve_mod.app = None
    client = TestClient(create_app())
    looped = client.post("/api/v1/loop", json={"steps": 2})
    assert looped.status_code == 200
    body = looped.json()
    result = body.get("result") if isinstance(body.get("result"), dict) else body
    actions = result.get("actions") or []
    assert "factor" in actions and "model" in actions
    posted = client.post("/api/v1/impl", json={"source": GOOD_SRC, "name": "http_iso"})
    assert posted.status_code == 200
    impl = posted.json()
    inner = impl.get("result") if isinstance(impl.get("result"), dict) else impl
    assert inner.get("ok") is True or inner.get("skipped") is True
    if inner.get("ok"):
        assert inner.get("catalog_id")


def test_doctor_reports_isolator_and_qlib_child(isolated_home: Path) -> None:
    from finaince.settings import doctor_report

    doc = doctor_report()
    assert "isolator" in doc
    assert "qlib_child" in doc
    assert isinstance(doc["isolator"].get("ok"), bool)
    assert isinstance(doc["qlib_child"].get("ok"), bool)
    qlib = evaluate(EvalRequest(expression="Rank($close)", dialect="qlib"))
    assert qlib.ok is False

from datetime import UTC, datetime
from pathlib import Path

from finaince.catalog.store import FactorCatalog
from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
from finaince.eval.router import EvalRequest, evaluate
from finaince.review.desk import approve, promote
from finaince.trace import list_chain


def test_adversary_happy_path(isolated_home: Path):
    # 1. Compute real metrics for "Rank(Delta(close, 7))"
    req = EvalRequest(
        expression="Rank(Delta(close, 7))",
        dialect="repro_polars",
        universe="local_panel",
    )
    res = evaluate(req)
    assert res.ok
    
    # 2. Seed catalog row
    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_adv_happy",
        name="adv_happy",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        universe="local_panel",
        metrics=FactorMetrics(
            ic=res.metrics.get("ic_mean"),
            sharpe=res.metrics.get("sharpe_ratio"),
        ),
        daily_returns=res.metrics.get("daily_returns", {}),
        lineage=FactorLineage(source="manual", source_ref="adv_happy"),
        created_at=now,
        updated_at=now,
    )
    cat = FactorCatalog()
    cat.upsert(rec)
    
    # 3. Promote
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    pid = promo["promotion_id"]
    
    # 4. Approve with adversary=True
    traces_before = len(list_chain())
    app = approve(pid, adversary=True, override=["thin_panel", "weak_ic", "inflated_sharpe"])
    assert app["ok"]
    assert "adversary" in app["gates"]
    assert app["gates"]["adversary"]["ok"]
    assert app["gates"]["adversary"]["verdict"] == "approved"
    
    # 5. Check trace event
    traces_after = list_chain()
    assert len(traces_after) > traces_before
    adv_events = [t for t in traces_after if t["action"] == "adversary_review"]
    assert len(adv_events) == 1
    assert adv_events[0]["metrics"]["verdict"] == "approved"

def test_adversary_tamper_case(isolated_home: Path):
    req = EvalRequest(
        expression="Rank(Delta(close, 7))",
        dialect="repro_polars",
        universe="local_panel",
    )
    res = evaluate(req)
    assert res.ok
    
    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_adv_tamper",
        name="adv_tamper",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        universe="local_panel",
        metrics=FactorMetrics(
            ic=0.99, # Wildly different from real IC
            sharpe=res.metrics.get("sharpe_ratio"),
        ),
        daily_returns=res.metrics.get("daily_returns", {}),
        lineage=FactorLineage(source="manual", source_ref="adv_tamper"),
        created_at=now,
        updated_at=now,
    )
    cat = FactorCatalog()
    cat.upsert(rec)
    
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    pid = promo["promotion_id"]
    
    app = approve(pid, adversary=True)
    assert not app["ok"]
    assert app["error"] == "adversary_rejected"
    assert app["adversary"]["verdict"] == "rejected"
    
    # Check which check failed
    ic_check = next(c for c in app["adversary"]["checks"] if c["name"] == "ic_match")
    assert not ic_check["ok"]
    
    # Row status unchanged
    rec_after = cat.get(rec.id)
    assert rec_after.status == "review"

def test_adversary_proxy_case(isolated_home: Path):
    req = EvalRequest(
        expression="Rank(Delta(close, 7))",
        dialect="repro_polars",
        universe="local_panel",
    )
    res = evaluate(req)
    assert res.ok
    
    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_adv_proxy",
        name="adv_proxy",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        universe="local_panel",
        metrics=FactorMetrics(
            ic=res.metrics.get("ic_mean"),
            sharpe=res.metrics.get("sharpe_ratio"),
        ),
        daily_returns=res.metrics.get("daily_returns", {}),
        lineage=FactorLineage(source="manual", source_ref="adv_proxy", formula_proxy=True),
        created_at=now,
        updated_at=now,
    )
    cat = FactorCatalog()
    cat.upsert(rec)
    
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    pid = promo["promotion_id"]
    
    app = approve(pid, adversary=True)
    assert not app["ok"]
    assert app["error"] == "adversary_rejected"
    
    proxy_check = next(c for c in app["adversary"]["checks"] if c["name"] == "not_proxy")
    assert not proxy_check["ok"]

def test_adversary_default_false(isolated_home: Path):
    req = EvalRequest(
        expression="Rank(Delta(close, 7))",
        dialect="repro_polars",
        universe="local_panel",
    )
    res = evaluate(req)
    assert res.ok
    
    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_adv_default",
        name="adv_default",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 7))"),
        universe="local_panel",
        metrics=FactorMetrics(
            ic=0.99, # Tampered, but adversary is off
            sharpe=res.metrics.get("sharpe_ratio"),
        ),
        daily_returns=res.metrics.get("daily_returns", {}),
        lineage=FactorLineage(source="manual", source_ref="adv_default"),
        created_at=now,
        updated_at=now,
    )
    cat = FactorCatalog()
    cat.upsert(rec)
    
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    pid = promo["promotion_id"]
    
    # Default is False, so it should pass (or fail on normal gates, but not adversary)
    # Wait, IC=0.99 might fail weak_ic gate? No, weak_ic fails if IC is too low.
    # But let's override gates just in case.
    app = approve(pid, override=["weak_ic", "inflated_sharpe", "thin_panel"])
    assert app["ok"]
    assert "adversary" not in app["gates"]

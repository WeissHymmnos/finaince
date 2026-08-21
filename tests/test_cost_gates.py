import math
from pathlib import Path

from finaince.baseline import run_locked_baseline
from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
from finaince.eval.router import EvalRequest, _evaluate
from finaince.review.gates import deflated_sharpe, ic_t_stat


def test_ic_t_stat():
    assert ic_t_stat(None, 400) is None
    assert ic_t_stat(0.3, None) is None
    assert math.isclose(ic_t_stat(0.3, 400), 6.0)

def test_deflated_sharpe():
    # <20 obs -> None
    short_returns = {f"d{i}": 0.01 for i in range(10)}
    assert deflated_sharpe(short_returns, 1) is None
    
    # high true SR with n_trials=1 -> ~high score
    good_returns = {f"d{i}": 0.01 for i in range(25)}
    good_returns["d0"] = -0.001 # add some variance
    dsr_1 = deflated_sharpe(good_returns, 1)
    assert dsr_1 is not None
    assert dsr_1 > 0.95
    
    # same returns but n_trials=1000 -> materially lower
    dsr_1000 = deflated_sharpe(good_returns, 1000)
    assert dsr_1000 is not None
    assert dsr_1000 < dsr_1
    assert dsr_1000 < 0.5

def test_baseline_cost():
    res1 = run_locked_baseline(cost_bps=5.0)
    res2 = run_locked_baseline(cost_bps=5.0)
    assert res1["ok"]
    assert res1["cost_bps"] == 5.0
    assert res1 == res2

def test_cost_math_eval(monkeypatch):
    # We need to mock build_backtest_bundle to return synthetic returns and turnover
    
    def mock_build(*args, **kwargs):
        # Create a dummy parquet file
        import tempfile
        import uuid

        import polars as pl
        
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "equity.parquet"
        
        df = pl.DataFrame({
            "date": ["2023-01-01", "2023-01-02", "2023-01-03"],
            "ls_return_raw": [0.01, 0.02, -0.01],
            "turnover": [0.1, 0.2, 0.1]
        })
        df.write_parquet(path)
        
        return {
            "backtest_id": str(uuid.uuid4()),
            "rows": 100,
            "ic_mean": 0.05,
            "ic_ir": 0.5,
            "sharpe_ratio": 1.0, # raw
            "max_drawdown": 0.1,
            "long_short_annual_return": 0.2,
            "equity_curve_path": str(path),
            "transaction_cost_bps": 0.0
        }
        
    import reproagent.reproducer.backtest_bundle as bb
    monkeypatch.setattr(bb, "build_backtest_bundle", mock_build)
    
    req = EvalRequest(expression="Rank(close)", dialect="repro_polars", cost_bps=5.0)
    res = _evaluate(req)
    
    assert res.ok
    assert res.metrics["cost_bps"] == 5.0
    assert "sharpe_net" in res.metrics
    assert res.metrics["sharpe_net"] < 7.0
    
    # Test warning when turnover missing
    def mock_build_no_turnover(*args, **kwargs):
        import tempfile
        import uuid

        import polars as pl
        
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "equity.parquet"
        
        df = pl.DataFrame({
            "date": ["2023-01-01", "2023-01-02", "2023-01-03"],
            "ls_return_raw": [0.01, 0.02, -0.01]
        })
        df.write_parquet(path)
        
        return {
            "backtest_id": str(uuid.uuid4()),
            "rows": 100,
            "ic_mean": 0.05,
            "ic_ir": 0.5,
            "sharpe_ratio": 1.0,
            "max_drawdown": 0.1,
            "long_short_annual_return": 0.2,
            "equity_curve_path": str(path),
            "transaction_cost_bps": 0.0
        }
        
    monkeypatch.setattr(bb, "build_backtest_bundle", mock_build_no_turnover)
    
    req2 = EvalRequest(expression="Rank(close)", dialect="repro_polars", cost_bps=5.0)
    res2 = _evaluate(req2)
    
    assert "cost_not_applied_no_turnover" in res2.warnings
    assert "sharpe_net" not in res2.metrics

def test_desk_approve_flow(monkeypatch):
    from datetime import UTC, datetime

    # Mock list_chain to return 1000 trials
    from finaince.catalog.store import FactorCatalog
    from finaince.review.desk import approve, promote
    def mock_list_chain(*args, **kwargs):
        return [{"action": "eval"} for _ in range(1000)]
    monkeypatch.setattr("finaince.trace.list_chain", mock_list_chain)
    
    now = datetime.now(UTC)
    
    # Strong but lucky single-window metrics
    import random
    import time
    random.seed(time.time())
    returns = {f"2023-01-{i:02d}": random.uniform(-0.02, 0.02) for i in range(1, 26)}
    
    import uuid
    uid = uuid.uuid4().hex[:8]
    rec = FactorRecord(
        id=f"fac_lucky_{uid}",
        name=f"lucky_{uid}",
        expression=FactorExpression(dialect="repro_polars", text="Rank(close)"),
        universe="local_panel",
        metrics=FactorMetrics(ic=0.05, extra={"ic_ir": 0.1}), # t_stat = 0.1 * sqrt(25) = 0.5 < 3.0
        daily_returns=returns,
        lineage=FactorLineage(source="manual", source_ref=f"lucky_{uid}"),
        created_at=now,
        updated_at=now,
    )
    
    cat = FactorCatalog()
    cat.upsert(rec)
    
    promo = promote(rec.id, direction="to_pool")
    assert promo["ok"]
    pid = promo["promotion_id"]
    
    # Approve should fail due to weak_ic and inflated_sharpe
    app = approve(pid)
    assert not app["ok"]
    assert "weak_ic" in app["gates"]["failures"]
    assert "inflated_sharpe" in app["gates"]["failures"]
    
    # Override path works
    app_override = approve(pid, override=["weak_ic", "inflated_sharpe", "thin_panel", "homogeneous"])
    assert app_override["ok"]
    
    # Thin data row -> gates skipped not failed
    rec_thin = FactorRecord(
        id="fac_thin",
        name="thin",
        expression=FactorExpression(dialect="repro_polars", text="Rank(close)"),
        universe="local_panel",
        metrics=FactorMetrics(ic=0.05),
        daily_returns={f"2023-01-{i:02d}": 0.01 for i in range(1, 10)}, # < 20 obs
        lineage=FactorLineage(source="manual", source_ref="thin"),
        created_at=now,
        updated_at=now,
    )
    cat.upsert(rec_thin)
    
    promo_thin = promote(rec_thin.id, direction="to_pool")
    pid_thin = promo_thin["promotion_id"]
    
    app_thin = approve(pid_thin, override=["thin_panel", "homogeneous"]) # override thin_panel so it doesn't fail on that
    assert app_thin["ok"]
    assert "weak_ic" not in app_thin["gates"]["failures"]
    assert "inflated_sharpe" not in app_thin["gates"]["failures"]
    assert app_thin["gates"]["details"]["weak_ic"] == "insufficient_for_t_stat"
    assert app_thin["gates"]["details"]["inflated_sharpe"] == "insufficient_returns"

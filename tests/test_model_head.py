import sys

import numpy as np
import pytest

from finaince.loop import train_linear_head
from finaince.model_head import train_head


def test_ols_delegation():
    np.random.seed(42)
    returns = {f"2020-01-{i:02d}": float(np.random.randn()) for i in range(1, 30)}
    
    res_ols = train_head(returns, kind="ols")
    res_loop = train_linear_head(returns)
    
    assert res_ols.keys() == res_loop.keys()
    assert res_ols["skipped"] == res_loop["skipped"]
    if not res_ols["skipped"]:
        assert abs(res_ols["portfolio_return"] - res_loop["portfolio_return"]) < 1e-12
        assert res_ols["model_config"] == res_loop["model_config"]

def test_default_env_unset(monkeypatch):
    monkeypatch.delenv("FINAINCE_MODEL_HEAD", raising=False)
    np.random.seed(42)
    returns = {f"2020-01-{i:02d}": float(np.random.randn()) for i in range(1, 30)}
    
    res_default = train_head(returns)
    res_loop = train_linear_head(returns)
    
    assert res_default["skipped"] == res_loop["skipped"]
    if not res_default["skipped"]:
        assert abs(res_default["portfolio_return"] - res_loop["portfolio_return"]) < 1e-12

def test_env_honored(monkeypatch):
    monkeypatch.setenv("FINAINCE_MODEL_HEAD", "gbm")
    np.random.seed(42)
    returns = {f"2020-01-{i:02d}": float(np.random.randn()) for i in range(1, 30)}
    
    res = train_head(returns)
    assert res["model_config"]["kind"] == "gbm"

def test_gbm_without_libs():
    try:
        import lightgbm
        pytest.skip("lightgbm is installed")
    except ImportError:
        pass
        
    try:
        import sklearn
        pytest.skip("sklearn is installed")
    except ImportError:
        pass
        
    returns = {f"2020-01-{i:02d}": 0.01 for i in range(1, 30)}
    res = train_head(returns, kind="gbm")
    
    assert res["skipped"] is True
    assert res["reason"] == "gbm_unavailable"

def test_gbm_real_path():
    try:
        import lightgbm
    except ImportError:
        try:
            import sklearn
        except ImportError:
            pytest.skip("Neither lightgbm nor sklearn is installed")
            
    np.random.seed(42)
    returns = {f"2020-01-{i:02d}": float(np.random.randn()) for i in range(1, 30)}
    res = train_head(returns, kind="gbm")
    
    assert res["skipped"] is False
    assert len(res["equity_curve"]) > 0
    assert "n_obs" in res["model_config"]

class FakeLGBMRegressor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        
    def fit(self, X, y):
        pass
        
    def predict(self, X):
        return np.ones(len(X)) * 0.01

def test_gbm_with_fake_lightgbm(monkeypatch):
    import types
    fake_lgbm = types.ModuleType("lightgbm")
    fake_lgbm.LGBMRegressor = FakeLGBMRegressor
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lgbm)
    
    np.random.seed(42)
    returns = {f"2020-01-{i:02d}": float(np.random.randn()) for i in range(1, 30)}
    
    res = train_head(returns, kind="gbm")
    
    assert res["skipped"] is False
    assert res["model_config"]["backend"] == "lightgbm"
    assert res["model_config"]["n_obs"] > 0
    assert len(res["equity_curve"]) > 0

def test_gbm_too_few_rows(monkeypatch):
    import types
    fake_lgbm = types.ModuleType("lightgbm")
    fake_lgbm.LGBMRegressor = FakeLGBMRegressor
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lgbm)
    
    returns = {f"2020-01-{i:02d}": 0.01 for i in range(1, 15)}
    res = train_head(returns, kind="gbm")
    
    assert res["skipped"] is True
    assert res["reason"] == "too_few_rows"

class CrashingLGBMRegressor:
    def __init__(self, **kwargs):
        pass
    def fit(self, X, y):
        raise ValueError("crash")
    def predict(self, X):
        return X

def test_gbm_fit_crash(monkeypatch):
    import types
    fake_lgbm = types.ModuleType("lightgbm")
    fake_lgbm.LGBMRegressor = CrashingLGBMRegressor
    monkeypatch.setitem(sys.modules, "lightgbm", fake_lgbm)
    
    returns = {f"2020-01-{i:02d}": 0.01 for i in range(1, 30)}
    res = train_head(returns, kind="gbm")
    
    assert res["skipped"] is True
    assert res["reason"].startswith("lightgbm_failed:")

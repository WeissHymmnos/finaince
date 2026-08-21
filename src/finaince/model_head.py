import os
from typing import Any


def _is_num(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True

def _portfolio_return(curve: dict[str, float]) -> float | None:
    if len(curve) < 2:
        return None
    keys = sorted(curve)
    start = float(curve[keys[0]])
    end = float(curve[keys[-1]])
    if start == 0:
        return None
    return end / start - 1.0

def train_head(returns: dict[str, float], *, kind: str | None = None) -> dict[str, Any]:
    """
    Train a predictive model head on factor returns.
    
    Args:
        returns: Dictionary mapping dates to returns.
        kind: Model kind ('ols' or 'gbm'). If None, reads FINAINCE_MODEL_HEAD env var.
        
    Returns:
        Dictionary with model configuration, equity curve, and portfolio return.
    """
    if kind is None:
        kind = os.environ.get("FINAINCE_MODEL_HEAD", "ols")
        
    if kind == "ols":
        from finaince.loop import train_linear_head
        return train_linear_head(returns)
    elif kind == "gbm":
        return _train_gbm_head(returns)
    else:
        from finaince.loop import train_linear_head
        res = train_linear_head(returns)
        if "model_config" in res:
            res["model_config"]["requested_kind"] = kind
        return res

def _train_gbm_head(returns: dict[str, float]) -> dict[str, Any]:
    config: dict[str, Any] = {"kind": "gbm", "lags": 5}
    
    backend = None
    ModelClass = None
    model_kwargs = {}
    
    try:
        import lightgbm
        backend = "lightgbm"
        ModelClass = lightgbm.LGBMRegressor
        model_kwargs = {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.05, "verbose": -1}
    except ImportError:
        try:
            import sklearn.ensemble
            backend = "sklearn_histgbm"
            ModelClass = sklearn.ensemble.HistGradientBoostingRegressor
            model_kwargs = {"max_depth": 3}
        except ImportError:
            return {"skipped": True, "reason": "gbm_unavailable", "model_config": config}
            
    config["backend"] = backend
    
    try:
        import numpy as np
    except ImportError:
        return {"skipped": True, "reason": "numpy_unavailable", "model_config": config}
        
    series = [(k, float(v)) for k, v in sorted(returns.items()) if _is_num(v)]
    
    lags = 5
    if len(series) < lags + 15:
        return {"skipped": True, "reason": "too_few_rows", "model_config": config}
        
    vals = np.array([v for _, v in series], dtype=float)
    keys = [k for k, _ in series]
    
    n_samples = len(vals) - lags
    X = np.zeros((n_samples, lags))
    for i in range(lags):
        X[:, i] = vals[lags - 1 - i : len(vals) - 1 - i]
    y = vals[lags:]
    y_keys = keys[lags:]
    
    min_train = 10
    preds = []
    realized = []
    pred_keys = []
    
    try:
        for t in range(min_train, len(y)):
            X_train = X[:t]
            y_train = y[:t]
            X_test = X[t:t+1]
            y_test = y[t]
            
            model = ModelClass(**model_kwargs)
            model.fit(X_train, y_train)
            pred = model.predict(X_test)[0]
            
            preds.append(pred)
            realized.append(y_test)
            pred_keys.append(y_keys[t])
    except Exception as exc:
        return {"skipped": True, "reason": f"{backend}_failed:{exc}", "model_config": config}
        
    preds = np.array(preds)
    realized = np.array(realized)
    pnl = preds * realized
    
    curve_vals = np.cumprod(1.0 + pnl)
    curve = {str(k): float(v) for k, v in zip(pred_keys, curve_vals, strict=False)}
    
    config["n_obs"] = len(preds)
    
    port = _portfolio_return(curve)
    return {
        "skipped": False,
        "model_config": config,
        "equity_curve": curve,
        "portfolio_return": port,
    }

"""Locked-window local-panel baseline. Not a CSI300 / paper-ARR claim."""

from __future__ import annotations

from typing import Any

LOCKED_WINDOW = {
    "start": "2023-01-03",
    "end": "2023-02-10",
    "universe": "local_panel",
    "cost_bps": 0,
    "dialect": "repro_polars",
    "expression": "Rank(Delta(close, 1))",
    "note": "fixture local_panel only; not CSI300 and not RD-Agent paper ARR",
}


def run_locked_baseline() -> dict[str, Any]:
    """Evaluate the locked expression on the shipped local fixture window."""
    import os

    from finaince.eval.router import EvalRequest, evaluate
    from finaince.runtime import packaged_local_panel

    spec = dict(LOCKED_WINDOW)
    packed = packaged_local_panel()
    if packed is None:
        return {
            "ok": False,
            "window": spec,
            "error": "missing_packaged_panel",
            "metrics": {"ic_mean": None, "sharpe_ratio": None, "rows": None, "universe_claim": spec["universe"]},
            "claim": spec["note"],
        }
    keys = ("LOCAL_DATA_PATH", "FINAINCE_LOCAL_DATA_PATH", "AIMINER_LOCAL_DATA_PATH")
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ[key] = str(packed)
    try:
        result = evaluate(
            EvalRequest(
                expression=str(spec["expression"]),
                dialect=str(spec["dialect"]),
                data_backend="local",
                universe=str(spec["universe"]),
                start=str(spec["start"]),
                end=str(spec["end"]),
                cost_bps=float(spec["cost_bps"]),
            )
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    metrics = dict(result.metrics or {})
    return {
        "ok": bool(result.ok),
        "window": spec,
        "error": result.error,
        "metrics": {
            "ic_mean": metrics.get("ic_mean"),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "rows": metrics.get("rows"),
            "universe_claim": metrics.get("universe_claim") or spec["universe"],
            "transaction_cost_bps": metrics.get("transaction_cost_bps"),
        },
        "claim": spec["note"],
    }

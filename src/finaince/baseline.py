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
    from finaince.eval.router import EvalRequest, evaluate

    spec = dict(LOCKED_WINDOW)
    result = evaluate(
        EvalRequest(
            expression=str(spec["expression"]),
            dialect=str(spec["dialect"]),
            data_backend="local",
            universe=str(spec["universe"]),
            start=str(spec["start"]),
            end=str(spec["end"]),
        )
    )
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
        },
        "claim": spec["note"],
    }

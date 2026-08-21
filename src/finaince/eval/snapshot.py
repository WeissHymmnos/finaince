"""Golden snapshot compare for repro_polars on the local fixture. Not engine parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from finaince.eval.router import EvalRequest, evaluate

DEFAULT_SNAPSHOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "eval_snapshot.json"
_EXPRS = [
    "Rank(Delta(close, 1))",
    "Ref(close, 20)",
    "Mean(close, 5)",
]


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def run_snapshot(path: Path | None = None) -> dict[str, Any]:
    snap_path = path or DEFAULT_SNAPSHOT
    expected = json.loads(snap_path.read_text(encoding="utf-8"))
    rows = []
    drifted = False
    for item in expected.get("expressions") or []:
        expr = str(item["expression"])
        out = evaluate(
            EvalRequest(
                expression=expr,
                dialect="repro_polars",
                data_backend="local",
                start=item.get("start"),
                end=item.get("end"),
                universe=item.get("universe") or "local_panel",
                cost_bps=3.0,
            )
        )
        got = {
            "expression": expr,
            "ok": out.ok,
            "ic_mean": out.metrics.get("ic_mean"),
            "sharpe_ratio": out.metrics.get("sharpe_ratio"),
            "rows": out.metrics.get("rows"),
        }
        match = (
            _close(got["ic_mean"], item.get("ic_mean"))
            and _close(got["sharpe_ratio"], item.get("sharpe_ratio"))
            and got["rows"] == item.get("rows")
        )
        if not match:
            drifted = True
        rows.append({**got, "expected": item, "match": match})
    return {"ok": not drifted, "drifted": drifted, "items": rows}

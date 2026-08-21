"""Adversarial reviewer: re-executes candidate evaluation in a fresh interpreter.

AgonAlpha-inspired machine-speed adversarial reviewer. Before a promotion is approved,
an independent fresh-interpreter process re-executes the candidate's evaluation and
verifies the recorded metrics. Fabricated or drifted numbers get vetoed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from finaince.catalog.store import FactorCatalog
from finaince.trace import append_event

_SNIPPET = """
import json
import sys
from finaince.eval.router import EvalRequest, evaluate

def main():
    try:
        config = json.loads(sys.argv[1])
        req = EvalRequest(
            expression=config["expression"],
            dialect=config["dialect"],
            universe=config["universe"],
            cost_bps=config.get("cost_bps", 0.0),
        )
        res = evaluate(req)
        out = {
            "ok": res.ok,
            "metrics": res.metrics,
            "daily_returns_count": len(res.metrics.get("daily_returns", {})),
        }
        print(json.dumps(out))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
"""

def adversarial_review(promotion_id: str, *, tol_rel: float = 0.05, tol_abs: float = 0.01) -> dict[str, Any]:
    """
    Re-execute evaluation in a fresh interpreter to verify recorded metrics.
    
    Tolerances:
    - ic_match: |child_ic - recorded_ic| <= max(tol_abs, tol_rel * |recorded_ic|)
    - sharpe_match: same tolerance on sharpe (if recorded sharpe is present)
    """
    cat = FactorCatalog()
    pending = [p for p in cat.list_promotions("pending") if p["id"] == promotion_id]
    if not pending:
        return {"ok": False, "verdict": "rejected", "checks": [], "error": "unknown promotion"}
    
    event = pending[0]
    rec = cat.get(event["catalog_id"])
    if not rec:
        return {"ok": False, "verdict": "rejected", "checks": [], "error": "unknown promotion"}

    cost_bps = rec.metrics.cost_drag or rec.metrics.extra.get("transaction_cost_bps", 0.0)
    
    config = {
        "expression": rec.expression.text,
        "dialect": rec.expression.dialect,
        "universe": rec.universe,
        "cost_bps": cost_bps,
    }
    
    checks = []
    
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SNIPPET, json.dumps(config)],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            checks.append({"name": "reexec_ok", "ok": False, "detail": "adversary_child_error"})
            child_res = None
        else:
            try:
                # Find the last line that is valid JSON
                lines = proc.stdout.strip().splitlines()
                child_res = json.loads(lines[-1])
                checks.append({"name": "reexec_ok", "ok": child_res.get("ok", False), "detail": ""})
            except (json.JSONDecodeError, IndexError):
                checks.append({"name": "reexec_ok", "ok": False, "detail": "adversary_child_error"})
                child_res = None
    except subprocess.TimeoutExpired:
        checks.append({"name": "reexec_ok", "ok": False, "detail": "adversary_timeout"})
        child_res = None

    if child_res:
        child_metrics = child_res.get("metrics", {})
        
        # ic_match
        recorded_ic = rec.metrics.ic
        child_ic = child_metrics.get("ic_mean")
        if recorded_ic is None or child_ic is None:
            checks.append({"name": "ic_match", "ok": False, "detail": "missing_ic"})
        else:
            diff = abs(child_ic - recorded_ic)
            allowed = max(tol_abs, tol_rel * abs(recorded_ic))
            checks.append({"name": "ic_match", "ok": diff <= allowed, "detail": f"diff={diff:.4f}"})
            
        # sharpe_match
        recorded_sharpe = rec.metrics.sharpe
        child_sharpe = child_metrics.get("sharpe_ratio")
        if recorded_sharpe is None:
            checks.append({"name": "sharpe_match", "ok": True, "detail": "skip_missing_recorded"})
        elif child_sharpe is None:
            checks.append({"name": "sharpe_match", "ok": False, "detail": "missing_child_sharpe"})
        else:
            diff = abs(child_sharpe - recorded_sharpe)
            allowed = max(tol_abs, tol_rel * abs(recorded_sharpe))
            checks.append({"name": "sharpe_match", "ok": diff <= allowed, "detail": f"diff={diff:.4f}"})
            
        # not_proxy
        checks.append({"name": "not_proxy", "ok": not rec.lineage.formula_proxy, "detail": ""})
        
        # returns_present
        returns_count = child_res.get("daily_returns_count", 0)
        checks.append({"name": "returns_present", "ok": returns_count > 0, "detail": f"count={returns_count}"})
    else:
        checks.append({"name": "ic_match", "ok": False, "detail": "no_child_res"})
        checks.append({"name": "sharpe_match", "ok": False, "detail": "no_child_res"})
        checks.append({"name": "not_proxy", "ok": False, "detail": "no_child_res"})
        checks.append({"name": "returns_present", "ok": False, "detail": "no_child_res"})

    all_ok = all(c["ok"] for c in checks)
    verdict = "approved" if all_ok else "rejected"
    
    failed_names = [c["name"] for c in checks if not c["ok"]]
    error_msg = ",".join(failed_names) if failed_names else None
    
    append_event(
        "adversary_review",
        metrics={"verdict": verdict, "n_checks": len(checks)},
        error=error_msg,
        summary=f"adversary {verdict}" + (f" failed={error_msg}" if error_msg else ""),
    )
    
    return {
        "ok": verdict == "approved",
        "verdict": verdict,
        "checks": checks,
        "promotion_id": promotion_id,
    }

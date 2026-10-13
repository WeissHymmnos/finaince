"""Phase 3 & Section 5 remediation tests (CODE_REVIEW_REPORT.md TC-* cases)."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest


def _record(rid: str, **kw: Any):
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorRecord

    now = datetime.now(UTC)
    return FactorRecord(
        id=rid,
        name=rid,
        expression=FactorExpression(dialect="repro_polars", text=kw.pop("text", "Rank(Delta(close, 7))")),
        lineage=FactorLineage(source="manual", source_ref=rid),
        created_at=now,
        updated_at=now,
        **kw,
    )


# ---------------------------------------------------------------------------
# Architecture / contract (TC-ARCH-01..03, TC-API-01)
# ---------------------------------------------------------------------------


def test_tc_arch_01_domain_layer_isolation_and_di() -> None:
    code = (
        "import sys; "
        "import finaince.domain.adapters, finaince.domain.scoring; "
        "bad = [m for m in ('finaince.eval.router','finaince.serve','finaince.catalog.store') if m in sys.modules]; "
        "assert not bad, bad"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    from finaince.domain.scoring import library_grade

    seen: dict[str, Any] = {}

    def scorer(expr: str, *, backtest_id: str | None = None) -> dict[str, Any]:
        seen.update(expr=expr, backtest_id=backtest_id)
        return {"score": 88.0, "grade": "A", "backtest_id": backtest_id}

    out = library_grade(expression="Rank(close)", backtest_id="bt-1", backtest_scorer=scorer)
    assert out["grade"] == "A"
    assert seen == {"expr": "Rank(close)", "backtest_id": "bt-1"}


def test_tc_arch_02_packaged_seed_zoo_present_and_used() -> None:
    from finaince import discovery

    packaged = Path(discovery.__file__).resolve().parent / "data" / "alpha_seed_zoo.json"
    assert packaged.is_file()
    seeds = discovery._seed_zoo_corpus()
    assert len(seeds) == len(json.loads(packaged.read_text())["seeds"]) > 0
    from finaince.review import gates

    assert len(gates._originality_corpus()) >= len(seeds)


def test_tc_arch_03_adversary_injects_src_pythonpath(isolated_home: Path, monkeypatch) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.review import adversary

    cat = FactorCatalog()
    cat.upsert(_record("fac_pp", engine="engine_native", window_start="2023-01-03", window_end="2023-02-10"))
    pid = cat.add_promotion("fac_pp", "to_pool", "pending", {})

    captured: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs.get("env") or {})
        out = json.dumps({"ok": True, "metrics": {"ic": 0.0}})
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(adversary.subprocess, "run", fake_run)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    adversary.adversarial_review(pid)
    src = str(Path(adversary.__file__).resolve().parents[2])
    assert src in captured.get("PYTHONPATH", "").split(os.pathsep)


def test_tc_api_01_eval_route_forwards_cost_bps(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from finaince.eval import router
    from finaince.eval.router import EvalResult
    from finaince.serve import create_app

    got: list[float] = []

    def fake_eval(req):
        got.append(req.cost_bps)
        return EvalResult(ok=True, dialect=req.dialect, data_backend=req.data_backend, metrics={"ic": 0.01})

    monkeypatch.setattr(router, "evaluate", fake_eval)
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer desk-test-token"}
    r = client.post("/api/v1/eval", json={"expression": "Rank(close)", "cost_bps": 25.0}, headers=headers)
    assert r.status_code == 200, r.text
    assert got == [25.0]
    assert r.json()["metrics"]["cost_bps"] == 25.0


# ---------------------------------------------------------------------------
# Quant correctness (TC-QUANT-01, 03, 04)
# ---------------------------------------------------------------------------


def test_tc_quant_01_reflect_sign_negates_returns_and_recomputes_mdd() -> None:
    from finaince.discovery import reflect_sign
    from finaince.domain.scoring import max_drawdown

    returns = {f"2024-01-{i:02d}": (0.04 if i <= 6 else -0.002) for i in range(1, 31)}
    orig_mdd = max_drawdown(list(returns.values()))
    neg_mdd = max_drawdown([-v for v in returns.values()])
    assert abs(orig_mdd - neg_mdd) > 0.02
    row = {
        "expression": "Rank(close)",
        "ic": -0.05,
        "ic_ir": -0.8,
        "sharpe": -1.5,
        "max_drawdown": orig_mdd,
        "returns": dict(returns),
        "metrics": {"ic": -0.05, "sharpe": -1.5, "max_drawdown": orig_mdd, "daily_returns": dict(returns)},
    }
    m = reflect_sign(row)
    assert m is not None and m["sign_reflected"] is True
    assert m["ic"] == pytest.approx(0.05)
    assert m["sharpe"] == pytest.approx(1.5)
    assert m["expression"] == "-(Rank(close))"
    for k, v in returns.items():
        assert m["returns"][k] == pytest.approx(-v)
        assert m["metrics"]["daily_returns"][k] == pytest.approx(-v)
    assert m["max_drawdown"] == pytest.approx(neg_mdd)
    assert m["metrics"]["max_drawdown"] == pytest.approx(neg_mdd)
    # original row untouched
    assert row["ic"] == -0.05


def _bars(code: str, closes: list[float], *, flat: set[int] = frozenset(), vol0: set[int] = frozenset()):
    rows = []
    for i, c in enumerate(closes):
        d = date(2024, 1, 2) + timedelta(days=i)
        if i in flat:
            o = h = lo = c
        else:
            o, h, lo = c * 0.999, c * 1.01, c * 0.99
        rows.append(
            {
                "trade_date": d,
                "ts_code": code,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 0.0 if i in vol0 else 1000.0,
                "amount": 1.0,
            }
        )
    return rows


def test_tc_quant_03_limit_up_and_suspension_withheld_from_forward_returns() -> None:
    from finaince.data_track import _forward_returns

    # A: day1 is a one-word +10% limit bar; B: day1 suspended; C: normal.
    rows = (
        _bars("A", [10.0, 11.0, 11.1], flat={1})
        + _bars("B", [10.0, 10.0, 10.2], vol0={1})
        + _bars("C", [10.0, 10.1, 10.2])
    )
    fwd = _forward_returns(pl.DataFrame(rows))
    day0 = fwd.filter(pl.col("trade_date") == date(2024, 1, 2))
    got = {r["ts_code"]: r["_fwd_ret"] for r in day0.iter_rows(named=True)}
    assert got["A"] is None  # cannot buy into a limit-up bar
    assert got["B"] is None  # suspended next day
    assert got["C"] == pytest.approx(0.01)


def test_tc_quant_04_train_linear_head_is_causal() -> None:
    from finaince.loop import train_linear_head

    base = date(2024, 1, 1)
    a = {(base + timedelta(days=i)).isoformat(): 0.002 * math.sin(i / 5.0) + 0.001 for i in range(70)}
    b = dict(a)
    for i in range(55, 70):
        b[(base + timedelta(days=i)).isoformat()] = -0.3
    ra, rb = train_linear_head(a, min_train=10), train_linear_head(b, min_train=10)
    assert not ra.get("skipped") and not rb.get("skipped")
    ca, cb = ra.get("equity_curve") or {}, rb.get("equity_curve") or {}
    cutoff = (base + timedelta(days=50)).isoformat()
    shared = [k for k in ca if k in cb and k <= cutoff]
    assert shared, "expected overlapping pre-perturbation equity points"
    for k in shared:
        assert ca[k] == pytest.approx(cb[k])


# ---------------------------------------------------------------------------
# Sandbox (TC-SEC-01/02)
# ---------------------------------------------------------------------------


def test_tc_sec_01_ast_whitelist_blocks_reflection_and_imports() -> None:
    from finaince.isolate import ASTSecurityError, validate_factor_ast

    bad = [
        "def compute(p):\n    return getattr(p, 'close')\n",
        "def compute(p):\n    return ().__class__.__base__.__subclasses__()\n",
        "import ctypes\ndef compute(p):\n    return p\n",
        "def compute(p):\n    return eval('1+1')\n",
    ]
    for src in bad:
        with pytest.raises((ASTSecurityError, ImportError)):
            validate_factor_ast(src)
    validate_factor_ast("def compute(p):\n    return [x * 2 for x in p['close']]\n")


def test_tc_sec_02_run_isolated_shares_single_timeout_budget(monkeypatch) -> None:
    from finaince import isolate

    monkeypatch.setattr(isolate, "isolator_available", lambda: {"ok": True})
    monkeypatch.setattr(isolate, "bwrap_available", lambda: True)
    monkeypatch.setattr(isolate, "sandbox_mode", lambda: "auto")
    monkeypatch.setattr(isolate, "_bwrap_command", lambda: ["bwrap-fake"])
    monkeypatch.setattr(isolate, "_child_command", lambda: ["child-fake"])

    seen: list[float] = []

    def fake_run(argv, **kwargs):
        seen.append(float(kwargs["timeout"]))
        time.sleep(0.2)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="bwrap failed")

    monkeypatch.setattr(isolate.subprocess, "run", fake_run)
    res = isolate.run_isolated("def compute(p):\n    return [1,2,3]\n", timeout=1.0)
    assert res["ok"] is False
    assert len(seen) == 2
    assert seen[1] < seen[0]
    assert seen[0] <= 1.0


# ---------------------------------------------------------------------------
# Resources / concurrency (TC-RES-01, TC-PERF-01, TC-CONC-01)
# ---------------------------------------------------------------------------


def test_tc_res_01_sqlite_fd_stable(isolated_home: Path) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.trace import append_event, list_chain

    fd_dir = Path(f"/proc/{os.getpid()}/fd")
    if not fd_dir.is_dir():
        pytest.skip("needs /proc")
    cat = FactorCatalog()
    cat.list()
    list_chain(limit=5)
    base = len(list(fd_dir.iterdir()))
    for i in range(200):
        cat.list()
        append_event("ping", summary=str(i))
    assert len(list(fd_dir.iterdir())) - base <= 5


def test_tc_perf_01_slow_eval_does_not_block_health(monkeypatch) -> None:
    import asyncio

    import httpx

    from finaince.eval import router
    from finaince.eval.router import EvalResult
    from finaince.serve import create_app

    def slow_eval(req):
        time.sleep(0.4)
        return EvalResult(ok=True, dialect=req.dialect, data_backend=req.data_backend, metrics={})

    monkeypatch.setattr(router, "evaluate", slow_eval)
    app = create_app()

    async def run() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            task = asyncio.create_task(
                c.post(
                    "/api/v1/eval",
                    json={"expression": "Rank(close)"},
                    headers={"Authorization": "Bearer desk-test-token"},
                )
            )
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            h = await c.get("/api/v1/health")
            ms = (time.perf_counter() - t0) * 1000
            assert h.status_code == 200
            assert (await task).status_code == 200
            return ms

    assert asyncio.run(run()) < 200.0


def test_tc_conc_01_start_process_respects_max_jobs(isolated_home: Path, monkeypatch) -> None:
    from finaince.jobs import runner

    monkeypatch.setenv("FINAINCE_MAX_JOBS", "2")

    class P:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    counter = iter(range(90001, 90100))
    counter_lock = threading.Lock()

    def fake_popen(argv, **kw):
        with counter_lock:
            return P(next(counter))

    monkeypatch.setattr(runner, "popen_detached", fake_popen)
    monkeypatch.setattr(runner, "pid_alive", lambda pid: True)
    monkeypatch.setattr(runner, "current_pgid", lambda pid=None: int(pid or 1))

    barrier = threading.Barrier(5)
    out: list[dict[str, Any]] = []

    def worker(i: int) -> None:
        barrier.wait()
        res = runner.start_process("research_loop", {"dedup_key": f"k{i}"}, ["true"])
        out.append(res)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for o in out if o.get("status") == "running") == 2
    assert sum(1 for o in out if o.get("error") == "max_jobs_reached") == 3


# ---------------------------------------------------------------------------
# Phase 3 audit regressions
# ---------------------------------------------------------------------------


def test_sync_cache_fetches_prior_december_constituents(isolated_home: Path, monkeypatch) -> None:
    from finaince import data_track

    asofs: list[date] = []
    monkeypatch.setattr(data_track, "fetch_year_live", lambda y: Path("."))
    monkeypatch.setattr(data_track, "fetch_components_live", lambda d: asofs.append(d) or Path("."))
    res = data_track.sync_cache(2020, 2020)
    assert res["ok"] is True
    assert date(2019, 12, 13) in asofs  # Dec 2019 snapshot needed for early-2020 PIT universe


def test_factor_scores_scrub_nan() -> None:
    from finaince.data_track import _factor_scores

    rows = []
    for i in range(30):
        c = 0.0 if i == 0 else 10.0 + i
        rows.extend(_bars(f"S{i:02d}", [c, c, c, c, c, c, c]))
    scored = _factor_scores(pl.DataFrame(rows), "vp_shock")
    assert scored["_score"].is_nan().sum() == 0


def test_combination_missing_dates_not_zero_filled_and_warmup_dropped() -> None:
    from finaince.combination import _rolling_inverse_vol_weights, _series, combine_daily

    base = date(2024, 1, 1)
    dates = [(base + timedelta(days=i)).isoformat() for i in range(60)]
    fac_a = {d: (0.02 if i % 2 else -0.02) for i, d in enumerate(dates) if i >= 30}
    fac_b = {d: (0.005 if i % 2 else -0.004) for i, d in enumerate(dates)}
    rows = _series({"fac_a": fac_a, "fac_b": fac_b}, dates)
    assert rows["fac_a"][0] is None
    w = _rolling_inverse_vol_weights(rows, lookback=20)
    assert w[0] == {}  # warmup: no weights
    out = combine_daily({"fac_a": fac_a, "fac_b": fac_b}, lookback=10, cost_bps=0.0)
    if out.get("ok"):
        assert dates[0] not in out["daily_returns"]
        assert out["n_days"] == len(out["daily_returns"])


def test_sweep_rank_by_is_prefix(tmp_path: Path) -> None:
    from finaince.sweep import top_specs_from_artifact

    def row(name: str, is_ric: float, oos_ric: float) -> dict[str, Any]:
        return {
            "ok": True,
            "name": name,
            "template": "mom",
            "field": "close",
            "window": 5,
            "expression": "Rank(Delta(close, 5))",
            "IS": {"rank_ic": is_ric},
            "OOS": {"rank_ic": oos_ric},
        }

    art = tmp_path / "s.json"
    art.write_text(json.dumps({"rows": [row("lowis", 0.01, 0.09), row("highis", 0.08, 0.02)]}))
    assert top_specs_from_artifact(art, 1, rank_by="is_rank_ic")[0]["name"] == "highis"
    assert top_specs_from_artifact(art, 1, rank_by="oos_rank_ic")[0]["name"] == "lowis"


def test_process_memory_concurrent_updates(isolated_home: Path) -> None:
    from finaince.process_memory import load_memory, record_edit_outcome

    motif = {"added_lines": 2, "removed_lines": 1, "changed_regions": 1, "signatures_equal": True}
    barrier = threading.Barrier(6)

    def worker() -> None:
        barrier.wait()
        for _ in range(5):
            record_edit_outcome(
                error_prefix="ZeroDivisionError", motif=motif, survived_gates=True, survived_adversary=True
            )

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    entries = list(load_memory()["motifs"].values())
    assert len(entries) == 1
    assert entries[0]["attempts"] == 30


def test_detect_local_date_range_alt_columns(tmp_path: Path) -> None:
    from finaince.runtime import detect_local_date_range

    pq = tmp_path / "prices.parquet"
    pl.DataFrame(
        {"instrument": ["a", "a"], "datetime": [date(2024, 2, 1), date(2024, 6, 30)], "close": [1.0, 2.0]}
    ).write_parquet(pq)
    assert detect_local_date_range(pq) == (date(2024, 2, 1), date(2024, 6, 30))


def test_terminate_process_tree_escalates_to_sigkill() -> None:
    from finaince.compat import pid_alive, popen_detached, terminate_process_tree

    proc = popen_detached(
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
    )
    time.sleep(0.4)
    assert pid_alive(proc.pid)
    terminate_process_tree(proc.pid, timeout=0.3)
    assert not pid_alive(proc.pid)


# ---------------------------------------------------------------------------
# Phase 3 roadmap: observable excepts, unified cost model, template semantics
# ---------------------------------------------------------------------------


def test_no_silent_broad_except_pass_in_src() -> None:
    """ISSUE-R4-04: broad ``except Exception: pass`` must log instead of vanishing."""
    import re

    pat = re.compile(
        r"^[ \t]*except (?:Exception|BaseException)(?: as \w+)?:[ \t]*(?:#[^\n]*)?\n[ \t]+pass\b"
        r"|^[ \t]*except (?:Exception|BaseException)(?: as \w+)?:[ \t]*pass\b",
        re.M,
    )
    offenders = [
        str(p)
        for p in Path("src/finaince").rglob("*.py")
        if pat.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_log_swallowed_emits_warning(caplog) -> None:
    import logging

    from finaince._swallow import log_swallowed

    with caplog.at_level(logging.WARNING, logger="finaince.swallowed"):
        log_swallowed("mod:1", ValueError("boom"))
    assert any("ValueError" in r.getMessage() and "mod:1" in r.getMessage() for r in caplog.records)


def test_cost_model_charges_both_legs() -> None:
    from finaince.domain.costs import ls_trading_cost, weight_l1_cost

    # mean leg churn 0.5 -> long churn + short churn = 1.0 of notional traded
    assert ls_trading_cost(0.5, 10.0) == pytest.approx(0.001)
    assert weight_l1_cost(1.0, 10.0) == pytest.approx(0.001)
    assert ls_trading_cost(0.0, 50.0) == 0.0


def test_window_metrics_net_cost_uses_unified_model() -> None:
    from finaince.data_track import _window_metrics

    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    series = [(d, 0.001 * (1 + (i % 3))) for i, d in enumerate(days)]
    churn = {d: 0.5 for d in days}
    free = _window_metrics(series, 0.0, churn)
    paid = _window_metrics(series, 10.0, churn)
    # 10bps * (0.5 * 2 legs) = 10bps/day drag -> annualized return drops by 0.001*252
    assert free["ar_net"] - paid["ar_net"] == pytest.approx(0.001 * 252.0, abs=1e-5)


def test_sweep_templates_use_rolling_operators_and_are_listed() -> None:
    from finaince.eval.dialects import attach_translation, translate_to_qlib
    from finaince.sweep import TEMPLATES

    for name in ("compression", "stoch"):
        text = TEMPLATES[name][1]("close", 5)
        assert "Ts_Max(" in text and "Ts_Min(" in text
        assert not any(tok in text.replace("Ts_Max(", "").replace("Ts_Min(", "") for tok in ("Max(", "Min("))
        assert attach_translation(text, "repro_polars")["translatable"] is True
    assert translate_to_qlib("-Rank(Ts_Max(close, 5) - Ts_Min(close, 5))") == (
        "-Rank(Max($close, 5) - Min($close, 5))"
    )


def test_engine_ts_max_is_rolling_but_max_is_elementwise() -> None:
    """Why [P1-07] matters: Max(x, n) in the engine is elementwise, Ts_Max is rolling."""
    from reproagent.reproducer import polars_engine as pe

    frame = pl.DataFrame({"asset": ["a"] * 5, "x": [1.0, 5.0, 2.0, 3.0, 4.0]})
    out = frame.select(
        pe.Ts_Max(pl.col("x"), 3).alias("ts"),
        pe.Max(pl.col("x"), 3).alias("el"),
    )
    assert out["ts"].to_list()[2:] == [5.0, 5.0, 4.0]
    assert out["el"].to_list() == [3.0, 5.0, 3.0, 3.0, 4.0]

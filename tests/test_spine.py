"""Spine contract: one scorer, one catalog provenance rule, one desk gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from finaince.cli import app
from tests.test_data_track import _seed_cache

EXPR = "Rank(Delta(close, 5))"
START = "2023-06-15"
END = "2023-12-20"


def _now() -> datetime:
    return datetime.now(UTC)


def _record(**kwargs):
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord

    payload = {
        "id": "fac_spine",
        "name": "spine",
        "expression": FactorExpression(dialect="repro_polars", text=EXPR),
        "universe": "csi300_pit",
        "metrics": FactorMetrics(ic=0.04, rank_icir=0.01),
        "daily_returns": {f"2024-03-{day:02d}": 0.001 * day for day in range(1, 28)},
        "lineage": FactorLineage(source="manual", source_ref="spine"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    payload.update(kwargs)
    return FactorRecord(**payload)


def test_eval_and_bench_agree_and_sweep_uses_same_core(isolated_home) -> None:
    _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)
    runner = CliRunner()
    evaluated = runner.invoke(
        app,
        [
            "eval",
            EXPR,
            "--dialect",
            "repro_polars",
            "--backend",
            "pit",
            "--start",
            START,
            "--end",
            END,
            "--cost-bps",
            "5",
        ],
    )
    assert evaluated.exit_code == 0, evaluated.stdout + evaluated.stderr
    eval_body = json.loads(evaluated.stdout)
    bench = runner.invoke(
        app,
        [
            "bench",
            "--expression",
            EXPR,
            "--is-start",
            START,
            "--is-end",
            END,
            "--oos-start",
            START,
            "--oos-end",
            END,
            "--cost-bps",
            "5",
        ],
    )
    assert bench.exit_code == 0, bench.stdout + bench.stderr
    bench_body = json.loads(bench.stdout)
    row = next(item for item in bench_body["table"] if item["factor"] == "expression")
    for key in ("ic", "rank_ic", "sharpe_net"):
        assert eval_body["metrics"][key] == row["IS"][key]
        assert abs(eval_body["metrics"][key] - row["OOS"][key]) <= 1e-6
    assert bench_body["provenance"]["turnover_model"] == "quintile_membership_churn"
    assert eval_body["metrics"]["turnover_model"] == "quintile_membership_churn"
    assert eval_body["metrics"]["engine"] == "pit_core"

    from datetime import date

    from finaince.sweep import _eval_chunk

    screened = _eval_chunk(
        (
            [
                {
                    "name": "mom_close_5",
                    "template": "mom",
                    "field": "close",
                    "window": 5,
                    "expression": EXPR,
                }
            ],
            {
                "is_start": START,
                "is_end": END,
                "oos_start": START,
                "oos_end": END,
                "cost_bps": 5.0,
                "embargo": True,
                "first_year": 2023,
                "last_year": 2024,
            },
        )
    )
    assert screened[0]["ok"] is True
    for key in ("ic", "rank_ic", "sharpe_net"):
        assert screened[0]["IS"][key] == row["IS"][key]
    assert date(2024, 6, 14).isoformat()


def test_rebalance_and_suspension_rules(isolated_home) -> None:
    from datetime import date

    from finaince.data_track import (
        rebalance_dates,
        tradable_members,
        write_components,
        write_suspensions,
    )

    dates = rebalance_dates(date(2024, 1, 1), date(2024, 12, 31))
    assert dates == [date(2024, 6, 14), date(2024, 12, 13)]
    write_components(date(2024, 6, 14), ["AAA", "BBB", "CCC"])
    suspended = date(2024, 6, 14)
    write_suspensions(suspended, ["BBB"])
    members = tradable_members(suspended, {date(2024, 6, 14): ["AAA", "BBB", "CCC"]})
    assert members == ["AAA", "CCC"]
    assert "BBB" not in members


def test_neutralization_without_industry_is_declared_raw(isolated_home) -> None:
    from finaince.data_track import run_bench

    _seed_cache(isolated_home, years=(2023,), n_assets=40)
    result = run_bench(
        is_start="2023-06-15",
        is_end="2023-12-20",
        oos_start="2023-06-15",
        oos_end="2023-12-20",
        cost_bps=5.0,
        neutralize_industry=True,
    )
    assert result["ok"] is True, result
    assert result["provenance"]["neutralization"] == "skipped_no_industry"
    assert result["provenance"]["neutralized"] is False
    assert all(row["neutralized"] is False for row in result["table"])
    assert result["table"][0]["IS"].get("ic") is not None


def test_unprovenanced_and_not_citable_block_promotion(isolated_home) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.review.desk import approve, promote
    from finaince.review.gates import evaluate_gates

    missing = _record(id="fac_missing", engine=None, window_start=None, window_end=None, citable=False)
    missing_gates = evaluate_gates(missing, direction="to_pool")
    assert "unprovenanced" in missing_gates["failures"]

    native = _record(
        id="fac_native",
        engine="engine_native",
        window_start="2023-01-01",
        window_end="2023-06-01",
        citable=False,
        metrics=__import__("finaince.domain.factor", fromlist=["FactorMetrics"]).FactorMetrics(
            ic=0.2, icir=2.0, rank_icir=2.0
        ),
    )
    native_gates = evaluate_gates(native, direction="to_pool", override=["thin_panel", "weak_ic", "inflated_sharpe", "homogeneous"])
    assert "not_citable" in native_gates["failures"]
    assert "unprovenanced" not in native_gates["failures"]
    catalog = FactorCatalog()
    catalog.upsert(native)
    promo = promote(native.id, direction="to_pool")
    denied = approve(promo["promotion_id"], override=["thin_panel", "weak_ic", "inflated_sharpe", "homogeneous"], adversary=False)
    assert denied["ok"] is False
    assert "not_citable" in denied["gates"]["failures"]

    weak = _record(
        id="fac_weak_field",
        engine="pit_core",
        window_start="2024-03-01",
        window_end="2024-03-28",
        citable=True,
    )
    weak_gates = evaluate_gates(weak, direction="to_pool", override=["thin_panel", "inflated_sharpe", "homogeneous"])
    assert "weak_ic" in weak_gates["failures"]
    assert "field=rank_icir" in weak_gates["details"]["weak_ic"]


def test_old_rows_migrate_to_unknown_and_stay_unapproved(isolated_home) -> None:
    import json
    import sqlite3
    from datetime import UTC, datetime

    from finaince.catalog.store import FactorCatalog
    from finaince.settings import get_settings

    now = datetime.now(UTC).isoformat()
    payload = {
        "id": "fac_old",
        "name": "old",
        "status": "candidate",
        "expression": {"dialect": "repro_polars", "text": "Rank(Delta(close, 1))", "input_fields": [], "validated": False, "translatable": False},
        "universe": "local_panel",
        "metrics": {"ic": 0.2},
        "daily_returns": {},
        "lineage": {"source": "manual", "source_ref": "old", "formula_proxy": False},
        "created_at": now,
        "updated_at": now,
    }
    db = get_settings().platform_db
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_catalog (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL,
                name TEXT, name_cn TEXT, status TEXT, dialect TEXT, formula TEXT,
                record_json TEXT NOT NULL, created_at TEXT, updated_at TEXT,
                UNIQUE(source, source_ref)
            )
            """
        )
        conn.execute(
            "INSERT INTO factor_catalog (id, source, source_ref, name, status, dialect, formula, record_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("fac_old", "manual", "old", "old", "candidate", "repro_polars", "Rank(Delta(close, 1))", json.dumps(payload), now, now),
        )
        conn.commit()
    loaded = FactorCatalog().get("fac_old")
    assert loaded is not None
    assert loaded.engine == "unknown"
    assert loaded.citable is False
    assert loaded.status == "candidate"


def test_loop_empty_queue_and_provenanced_candidates(isolated_home) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.loop import run_loop
    from finaince.trace import list_chain

    empty = run_loop(steps=2)
    assert empty["degraded"] == "expression_queue_empty"
    assert empty["expressions_evaluated"] == []
    assert empty["actions"] == []
    assert "Rank(Delta(close, 1))" not in json.dumps(empty)

    _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)
    out = run_loop(steps=2, expressions=[EXPR])
    assert out["expressions_evaluated"]
    assert out["expressions_evaluated"][0]["expression"] == EXPR
    assert all(item["expression"] != "Rank(Delta(close, 1))" for item in out["expressions_evaluated"])
    catalog_id = out["expressions_evaluated"][0]["catalog_id"]
    assert catalog_id
    record = FactorCatalog().get(catalog_id)
    assert record is not None
    assert record.status == "candidate"
    assert record.engine == "pit_core"
    assert record.window_start and record.window_end
    assert record.metrics.cost_bps == 5.0
    assert record.hypothesis
    assert FactorCatalog().find_by_expr_hash(EXPR, "repro_polars")
    model_events = [ev for ev in list_chain() if ev.get("action") == "loop_model"]
    assert model_events, out
    hypothesis = model_events[-1].get("hypothesis") or ""
    assert "curve-timing" in hypothesis
    assert "not model research" in hypothesis
    help_out = CliRunner().invoke(app, ["loop", "--help"])
    assert help_out.exit_code == 0, help_out.stdout + help_out.stderr
    assert "curve-timing" in help_out.stdout
    assert "not model research" in help_out.stdout


def test_incompatible_windows(isolated_home) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.combination import try_combine_ready
    from finaince.domain.factor import FactorMetrics

    left = _record(
        id="fac_w1",
        engine="pit_core",
        citable=True,
        window_start="2023-01-01",
        window_end="2023-06-30",
        status="ready",
        metrics=FactorMetrics(ic=0.05, rank_icir=1.0),
        daily_returns={f"2023-01-{day:02d}": 0.01 for day in range(1, 28)},
    )
    right = _record(
        id="fac_w2",
        engine="pit_core",
        citable=True,
        window_start="2024-01-01",
        window_end="2024-06-30",
        status="ready",
        metrics=FactorMetrics(ic=0.05, rank_icir=1.0),
        daily_returns={f"2024-01-{day:02d}": 0.01 for day in range(1, 28)},
        lineage=left.lineage.model_copy(update={"source_ref": "spine-b"}),
    )
    catalog = FactorCatalog()
    catalog.upsert(left)
    catalog.upsert(right)
    # status ready is stored on the record; list(status=ready) reads the column.
    combined = try_combine_ready()
    assert combined["reason"] == "incompatible_windows"


def test_baseline_stays_locked_probe(isolated_home) -> None:
    from finaince.baseline import LOCKED_WINDOW, run_locked_baseline

    out = run_locked_baseline()
    assert out["window"]["expression"] == "Rank(Delta(close, 1))"
    assert out["window"]["cost_bps"] == 0
    assert out["window"]["universe"] == "local_panel"
    assert out["window"]["expression"] == LOCKED_WINDOW["expression"]
    assert "citable" not in out["claim"].lower()


def test_isolate_scores_before_catalog_and_failure_is_traced(isolated_home) -> None:
    from finaince.catalog.store import FactorCatalog
    from finaince.isolate import run_isolated, upsert_isolated
    from finaince.trace import list_chain

    failed = run_isolated("import socket\n", name="net")
    assert failed["ok"] is False
    assert any(ev.get("error") for ev in list_chain() if ev.get("action") == "isolated_impl")
    blocked = upsert_isolated({"ok": True, "name": "bare", "expression": None, "values": [1, 2, 3]})
    assert blocked["ok"] is False
    assert blocked["error"] == "not_scored"
    assert FactorCatalog().list() == []

    _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)
    scored = upsert_isolated(
        {"ok": True, "name": "scored", "expression": EXPR, "values": [1.0, 2.0, 3.0]},
        universe="csi300_pit",
    )
    assert scored["ok"] is True, scored
    assert scored["record"].engine == "pit_core"
    assert scored["record"].status == "candidate"


def test_cli_no_adversary_skips_reexec(isolated_home) -> None:
    """The CLI flag is the only skip. The default review command still re-evaluates."""
    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import FactorMetrics
    from finaince.review.desk import promote

    def _pending(catalog_id: str, month: int):
        record = _record(
            id=catalog_id,
            engine="pit_core",
            window_start=START,
            window_end=END,
            citable=True,
            metrics=FactorMetrics(ic=0.11, rank_icir=2.0, sharpe_net=1.0, cost_bps=5.0),
            daily_returns=(
                {f"2023-06-{day:02d}": 0.01 + day / 1000 for day in range(1, 28)}
                if month == 6
                else {f"2023-08-{day:02d}": ((-1) ** day) * 0.02 for day in range(1, 28)}
            ),
        )
        FactorCatalog().upsert(record)
        promo = promote(record.id, direction="to_pool", yes=True)
        assert promo["ok"], promo
        return promo["promotion_id"]

    runner = CliRunner()
    flags = ["--override", "thin_panel,weak_ic,inflated_sharpe,homogeneous"]
    skipped = runner.invoke(
        app,
        ["review", "--approve", _pending("fac_cli_skip", 6), *flags, "--no-adversary"],
    )
    assert skipped.exit_code == 0, skipped.stdout + skipped.stderr
    skipped_body = json.loads(skipped.stdout)
    assert skipped_body["ok"] is True, skipped_body
    assert "adversary" not in skipped_body["gates"]

    default = runner.invoke(app, ["review", "--approve", _pending("fac_cli_default", 8), *flags])
    assert default.exit_code == 0, default.stdout + default.stderr
    default_body = json.loads(default.stdout)
    assert default_body["ok"] is False, default_body
    assert default_body["error"] == "adversary_rejected"


def test_approve_adversary_http_and_sandbox(isolated_home) -> None:
    from fastapi.testclient import TestClient

    from finaince.isolate import _bwrap_command, run_isolated
    from finaince.serve import create_app
    from tests.conftest import desk_headers

    assert "--share-net" not in _bwrap_command()
    net = run_isolated("import socket\ndef compute(panel):\n    return [1.0, 2.0, 3.0]\n", name="sock")
    assert net["ok"] is False

    client = TestClient(create_app())
    override = client.post(
        "/api/v1/review/missing/approve",
        json={"override": ["thin_panel"]},
        headers=desk_headers(),
    )
    assert override.status_code == 403
    skipped = client.post(
        "/api/v1/review/missing/approve",
        json={"adversary": False},
        headers=desk_headers(),
    )
    assert skipped.status_code == 400


def test_adversary_reexec_matches_pit_window_and_cost(isolated_home) -> None:
    """A citable pit row is re-scored on its own window, backend, and cost."""
    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
    from finaince.eval.router import EvalRequest, evaluate
    from finaince.review.desk import approve, promote

    _seed_cache(isolated_home, years=(2023, 2024), n_assets=40)
    scored = evaluate(
        EvalRequest(
            expression=EXPR,
            dialect="repro_polars",
            data_backend="pit",
            start=START,
            end=END,
            cost_bps=5.0,
        )
    )
    assert scored.ok, scored
    assert scored.metrics["engine"] == "pit_core"
    assert scored.metrics["cost_bps"] == 5.0
    now = _now()
    record = FactorRecord(
        id="fac_pit_adv",
        name="pit_adv",
        expression=FactorExpression(dialect="repro_polars", text=EXPR, validated=True),
        universe="csi300_pit",
        engine="pit_core",
        window_start=START,
        window_end=END,
        citable=True,
        turnover_model=scored.metrics.get("turnover_model"),
        metrics=FactorMetrics(
            ic=scored.metrics["ic"],
            sharpe=scored.metrics.get("sharpe_ratio"),
            sharpe_net=scored.metrics.get("sharpe_net"),
            cost_bps=5.0,
            rank_icir=scored.metrics.get("rank_icir"),
        ),
        daily_returns=dict(scored.metrics.get("daily_returns") or {}),
        lineage=FactorLineage(source="manual", source_ref="pit-adv"),
        created_at=now,
        updated_at=now,
    )
    FactorCatalog().upsert(record)
    promo = promote(record.id, direction="to_pool")
    assert promo["ok"]
    decision = approve(
        promo["promotion_id"],
        override=["thin_panel", "weak_ic", "inflated_sharpe", "homogeneous"],
    )
    assert decision["ok"] is True, decision
    checks = {item["name"]: item for item in decision["gates"]["adversary"]["checks"]}
    assert checks["reexec_ok"]["ok"] is True
    assert checks["ic_match"]["ok"] is True
    assert checks["sharpe_match"]["ok"] is True
    assert abs(record.metrics.ic - scored.metrics["ic"]) <= 1e-6
    assert abs(record.metrics.sharpe - scored.metrics["sharpe_net"]) <= 1e-6


def test_finished_job_lists_its_trace_id(isolated_home) -> None:
    from fastapi.testclient import TestClient

    from finaince.jobs.runner import list_jobs, submit
    from finaince.serve import create_app
    from finaince.trace import event_id_for_job
    from tests.conftest import desk_headers

    finished = submit("trace_probe", {"note": "spine"}, run=lambda: {"ok": True, "n": 1})
    assert finished["status"] == "done"
    assert finished["trace_id"]
    assert finished["trace_id"] == event_id_for_job(finished["id"])
    listed = next(row for row in list_jobs() if row["id"] == finished["id"])
    assert listed["trace_id"] == finished["trace_id"]
    client = TestClient(create_app())
    body = client.get("/api/v1/jobs", headers=desk_headers())
    assert body.status_code == 200
    remote = next(row for row in body.json()["items"] if row["id"] == finished["id"])
    assert remote["trace_id"] == finished["trace_id"]
    page = Path(__file__).resolve().parents[1].joinpath("src/finaince/web/index.html").read_text()
    assert "job.trace_id" in page


def test_campaign_brain_and_memory_credit(isolated_home) -> None:
    from finaince.brain_track import write_back
    from finaince.catalog.store import FactorCatalog
    from finaince.corpus_campaign import apply_report_outcome, ready_pit_core_count, stats_summary
    from finaince.domain.factor import FactorMetrics
    from finaince.process_memory import record_edit_outcome

    outcome = apply_report_outcome({"status": "no_factors", "factors": [{"id": "should-not"}]})
    assert outcome["ready_created"] == 0
    assert outcome["catalog_ids"] == []
    assert ready_pit_core_count() == 0
    assert stats_summary()["ready_pit_core"] == 0

    record = _record(
        id="fac_brain",
        engine="pit_core",
        citable=True,
        window_start="2023-01-01",
        window_end="2023-12-31",
        metrics=FactorMetrics(ic=0.11, rank_icir=1.2),
    )
    FactorCatalog().upsert(record)
    written = write_back(
        record.id,
        {"ok": True, "alpha_id": "A1", "grade": "PASS", "adjudication_level": "platform"},
    )
    assert written["ok"] is True
    loaded = FactorCatalog().get(record.id)
    assert loaded.metrics.ic == 0.11
    assert loaded.lineage.brain_alpha_id == "A1"
    assert loaded.lineage.brain_grade == "PASS"

    motif = {"added_lines": 1, "removed_lines": 0, "changed_regions": 1, "signatures_equal": False}
    gates_only = record_edit_outcome(error_prefix="E: a", motif=motif, survived_gates=True, survived_adversary=False)
    both = record_edit_outcome(error_prefix="E: a", motif=motif, survived_gates=True, survived_adversary=True)
    assert gates_only["weight"] < 0
    assert both["weight"] > gates_only["weight"]

"""Unified CLI: discover (aiminer) and reproduce (reproagent)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

from finaince import __version__
from finaince._paths import ensure_import_paths

ensure_import_paths()

app = typer.Typer(
    name="finaince",
    help="FinAlpha (finaince) — aiminer discovery swarm + reproagent 研报复现 + catalog/eval/review",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"finaince {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version",
    ),
) -> None:
    """finaince CLI."""


def _demo_candidates() -> list[dict[str, Any]]:
    shared = {f"2024-01-{day:02d}": float(day) / 100.0 for day in range(1, 13)}
    weak = {f"2024-02-{day:02d}": 0.001 for day in range(1, 13)}
    return [
        {
            "role": "momentum specialist",
            "hypothesis": "trend-follow-close",
            "perf_metric": 0.04,
            "selection_score": 0.04,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "mean-reversion specialist",
            "hypothesis": "correlated-duplicate",
            "perf_metric": 0.03,
            "selection_score": 0.03,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "noise trader",
            "hypothesis": "below-ic-threshold",
            "perf_metric": 0.001,
            "selection_score": 0.001,
            "market_profile": "cn_stock",
            "returns": weak,
        },
    ]


@app.command(
    "discover",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def discover_cmd(
    ctx: typer.Context,
    demo: bool = typer.Option(
        False,
        "--demo",
        help="No-LLM dry path: score a factor and run IC/correlation pool cull",
    ),
    cull_json: Optional[Path] = typer.Option(
        None,
        "--cull-json",
        exists=True,
        dir_okay=False,
        help="JSON file of candidate factor dicts for evaluate_and_combine cull",
    ),
    swarm: bool = typer.Option(
        False,
        "--swarm",
        help="Launch the shipped aiminer manager swarm (LLM + backtest)",
    ),
    sync: bool = typer.Option(
        True,
        "--sync/--async",
        help="Record a platform job; --sync (default) runs the engine in-process",
    ),
) -> None:
    """Factor discovery (aiminer manager/swarm, selection score, pool cull)."""
    from finaince.discovery import cull_factor_pool, score_factor

    if swarm:
        from finaince.jobs.runner import run_swarm_job

        out = run_swarm_job(list(ctx.args) if ctx.args else None, sync=sync)
        typer.echo(json.dumps(out, default=str, ensure_ascii=False))
        if out.get("status") == "error":
            raise typer.Exit(code=1)
        return

    if cull_json is not None:
        payload = json.loads(cull_json.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            typer.echo("cull-json must be a JSON list of factor dicts", err=True)
            raise typer.Exit(code=1)
        kept = cull_factor_pool(payload)
        typer.echo(
            json.dumps(
                {
                    "action": "cull",
                    "input_count": len(payload),
                    "kept_count": len(kept),
                    "kept": [
                        {
                            "id": item.get("id"),
                            "hypothesis": item.get("hypothesis"),
                            "perf_metric": item.get("perf_metric"),
                            "decision": "keep",
                        }
                        for item in kept
                    ],
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return

    if not demo:
        typer.echo(
            "discover: pass --demo, --cull-json, or --swarm "
            "(bare discover is not a real mine)",
            err=True,
        )
        raise typer.Exit(code=2)

    if demo:
        metrics = {
            "annualized_return": 0.18,
            "sharpe": 1.2,
            "max_drawdown": 0.12,
            "turnover": 0.4,
            "cost_drag": 0.01,
        }
        score = score_factor(metrics, factor_ic=0.03)
        candidates = _demo_candidates()
        kept = cull_factor_pool(candidates)
        kept_names = {item.get("hypothesis") for item in kept}
        decisions = []
        for item in candidates:
            name = item.get("hypothesis")
            decisions.append(
                {
                    "hypothesis": name,
                    "perf_metric": item.get("perf_metric"),
                    "decision": "keep" if name in kept_names else "cull",
                    "id": next(
                        (k.get("id") for k in kept if k.get("hypothesis") == name),
                        None,
                    ),
                }
            )
        typer.echo(
            json.dumps(
                {
                    "action": "discover",
                    "score": score,
                    "metrics": metrics,
                    "pool": decisions,
                    "kept_count": len(kept),
                },
                ensure_ascii=False,
                default=str,
            )
        )


@app.command("reproduce")
def reproduce_cmd(
    pdf_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    sync: bool = typer.Option(
        True,
        "--sync/--async",
        help="Record a platform job; --sync (default) runs the pipeline in-process",
    ),
    start: Optional[str] = typer.Option(None, "--start", help="Backtest start YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", help="Backtest end YYYY-MM-DD"),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Price source: ricequant | local (default: runtime resolver)",
    ),
) -> None:
    """研报复现: ingest/parse → reproduce/backtest → deviation → library."""
    import os
    from datetime import date

    from finaince.jobs.runner import run_reproduce_job
    from finaince.runtime import default_backtest_window, resolve_data_source

    if source:
        os.environ["FINAINCE_DATA_SOURCE"] = source
    kwargs: dict[str, Any] = default_backtest_window(source or resolve_data_source())
    if start:
        kwargs["start_date"] = date.fromisoformat(start)
    if end:
        kwargs["end_date"] = date.fromisoformat(end)
    if source:
        kwargs["data_source"] = source
    out = run_reproduce_job(str(pdf_path), sync=sync, backtest_kwargs=kwargs)
    if out.get("status") == "error":
        typer.echo(f"reproduce failed: {out.get('error')}", err=True)
        raise typer.Exit(code=1)
    if sync:
        typer.echo("reproduce ok")
        result = out.get("result")
        if result is not None:
            typer.echo(json.dumps(result, default=str, ensure_ascii=False))
        return
    typer.echo(json.dumps(out, default=str, ensure_ascii=False))


@app.command("validate")
def validate_cmd(expression: str = typer.Argument(...)) -> None:
    """Validate a factor expression (reproagent polars engine)."""
    from finaince.reproduction import validate_expression

    typer.echo(json.dumps(validate_expression(expression), ensure_ascii=False))


@app.command("library")
def library_cmd(
    query: str = typer.Option("", "--query", "-q", help="Name substring"),
    style: Optional[str] = typer.Option(None, "--style", "-s"),
) -> None:
    """Search the unified catalog first, then the engine library."""
    from finaince.tools import handle_search_library

    hits = handle_search_library(query=query, style=style)
    typer.echo(json.dumps(hits, ensure_ascii=False, default=str))


@app.command("catalog")
def catalog_cmd(
    action: Optional[str] = typer.Argument(None, help="rebuild — refresh from engine stores"),
    source: Optional[str] = typer.Option(None, "--source"),
    query: str = typer.Option("", "--query", "-q"),
    rebuild: bool = typer.Option(False, "--rebuild"),
    rebuild_source: Optional[str] = typer.Option(None, "--rebuild-source"),
    retag_synthetic: bool = typer.Option(
        False,
        "--retag-synthetic",
        help="Mark discovery/synthetic library writes; leave real reports alone",
    ),
) -> None:
    """Unified factor catalog (discovery | reproduction)."""
    if action == "rebuild":
        rebuild = True
    if retag_synthetic:
        from finaince.catalog.rebuild import retag_synthetic as do_retag

        out: dict[str, Any] = do_retag()
        if rebuild or rebuild_source:
            from finaince.catalog.rebuild import rebuild as do_rebuild

            out["rebuild"] = do_rebuild(source=rebuild_source or source)
        typer.echo(json.dumps(out, default=str))
        return
    if rebuild or rebuild_source:
        from finaince.catalog.rebuild import rebuild as do_rebuild

        typer.echo(json.dumps(do_rebuild(source=rebuild_source or source), default=str))
        return
    from finaince.catalog.store import FactorCatalog

    items = FactorCatalog().list(source=source, query=query)
    typer.echo(
        json.dumps(
            {"count": len(items), "items": [i.model_dump(mode="json") for i in items]},
            default=str,
            ensure_ascii=False,
        )
    )


@app.command("eval")
def eval_cmd(
    expression: str = typer.Argument(...),
    dialect: str = typer.Option("repro_polars", "--dialect"),
    backend: str = typer.Option(
        "auto",
        "--backend",
        help="local | ricequant | auto (ricequant when creds exist and mock is off)",
    ),
    start: Optional[str] = typer.Option(None, "--start"),
    end: Optional[str] = typer.Option(None, "--end"),
    snapshot: bool = typer.Option(False, "--snapshot"),
    engine_parity: bool = typer.Option(
        False,
        "--engine-parity",
        help="Compare repro_polars vs optional 3.10 qlib subprocess",
    ),
) -> None:
    """Evaluate an expression via the (dialect, backend) router."""
    from finaince.eval.router import EvalRequest, evaluate
    from finaince.runtime import default_universe, resolve_data_source

    if engine_parity:
        from finaince.eval.qlib_subprocess import compare_engines

        data_backend = resolve_data_source() if backend == "auto" else backend
        out = compare_engines(
            expression,
            start=start,
            end=end,
            data_backend=data_backend,
            universe=default_universe(data_backend),
        )
        typer.echo(json.dumps(out, default=str, ensure_ascii=False))
        if out.get("skipped"):
            return
        if not out.get("ok"):
            raise typer.Exit(code=1)
        return

    if snapshot:
        from finaince.eval.snapshot import run_snapshot

        out = run_snapshot()
        typer.echo(json.dumps(out, default=str, ensure_ascii=False))
        if not out.get("ok"):
            raise typer.Exit(code=1)
        return

    data_backend = resolve_data_source() if backend == "auto" else backend
    result = evaluate(
        EvalRequest(
            expression=expression,
            dialect=dialect,
            data_backend=data_backend,
            start=start,
            end=end,
            universe=default_universe(data_backend),
        )
    )
    typer.echo(
        json.dumps(
            {
                "ok": result.ok,
                "dialect": result.dialect,
                "metrics": result.metrics,
                "error": result.error,
                "translatable": result.translatable,
                "alt_text": result.alt_text,
                "warnings": result.warnings,
            },
            default=str,
            ensure_ascii=False,
        )
    )
    if not result.ok:
        raise typer.Exit(code=3 if dialect == "qlib" else 1)


@app.command("promote")
def promote_cmd(
    catalog_id: str = typer.Argument(...),
    direction: str = typer.Option("to_pool", "--to", help="to_pool or to_library"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Submit a catalog row for review (does not write the other SoR)."""
    from finaince.review.desk import promote

    typer.echo(json.dumps(promote(catalog_id, direction=direction, yes=yes), default=str))


@app.command("review")
def review_cmd(
    approve_id: Optional[str] = typer.Option(None, "--approve"),
    reject_id: Optional[str] = typer.Option(None, "--reject"),
    override: Optional[str] = typer.Option(None, "--override", help="Comma list, e.g. thin_panel"),
) -> None:
    """List pending promotions or approve/reject one."""
    from finaince.catalog.store import FactorCatalog
    from finaince.review.desk import approve, reject

    overrides = [part.strip() for part in (override or "").split(",") if part.strip()]
    if approve_id:
        typer.echo(json.dumps(approve(approve_id, override=overrides), default=str))
        return
    if reject_id:
        typer.echo(json.dumps(reject(reject_id), default=str))
        return
    typer.echo(json.dumps({"items": FactorCatalog().list_promotions("pending")}, default=str))


@app.command("jobs")
def jobs_cmd(
    cancel_id: Optional[str] = typer.Option(None, "--cancel", help="Cancel a running job by id"),
) -> None:
    """List platform jobs or cancel one (process tree on POSIX / taskkill on Windows)."""
    from finaince.jobs.runner import cancel, list_jobs

    if cancel_id:
        typer.echo(json.dumps(cancel(cancel_id), default=str))
        return
    rows = list_jobs()
    typer.echo(json.dumps({"count": len(rows), "items": rows}, default=str))


@app.command("doctor")
def doctor_cmd(
    audit_check: bool = typer.Option(False, "--audit-check"),
    watch: bool = typer.Option(False, "--watch", help="Refresh health on an interval"),
    iterations: int = typer.Option(1, "--iterations", help="Watch ticks (min 1)"),
    interval: float = typer.Option(2.0, "--interval", help="Seconds between watch ticks"),
) -> None:
    """Check FinAlpha home, settings, and provider mapping."""
    import time

    from finaince.settings import doctor_report

    ticks = max(1, int(iterations)) if watch else 1
    last: dict[str, Any] | None = None
    for idx in range(ticks):
        last = doctor_report(audit_check=audit_check)
        last["tick"] = idx + 1
        last["watch"] = bool(watch)
        typer.echo(json.dumps(last, default=str, indent=2))
        if idx + 1 < ticks:
            time.sleep(max(0.0, float(interval)))
    if last is not None and not last.get("ok"):
        raise typer.Exit(code=1)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Same-origin workbench + /api/v1 (and aiminer /api when available)."""
    from finaince.auth import validate_serve_host
    from finaince.serve import main as serve_main

    serve_main(host=validate_serve_host(host), port=port)


@app.command("trace")
def trace_cmd(limit: int = typer.Option(20, "--limit")) -> None:
    """List the causal research chain for this FINAINCE_HOME."""
    from finaince.trace import list_chain

    items = list_chain(limit=limit)
    typer.echo(json.dumps({"count": len(items), "items": items}, default=str, ensure_ascii=False, indent=2))


@app.command("impl")
def impl_cmd(
    source_file: Path = typer.Argument(..., exists=True, dir_okay=False),
    name: str = typer.Option("isolated", "--name"),
    universe: str = typer.Option("local_panel", "--universe"),
) -> None:
    """Run an isolated compute(panel) implementation and upsert catalog."""
    from finaince.jobs.runner import run_impl_job

    out = run_impl_job(source_file.read_text(encoding="utf-8"), name=name, universe=universe)
    typer.echo(json.dumps(out, default=str, ensure_ascii=False))
    result = out.get("result") if isinstance(out.get("result"), dict) else out
    if not (out.get("ok") or (isinstance(result, dict) and result.get("ok"))):
        raise typer.Exit(code=1)


@app.command("baseline")
def baseline_cmd() -> None:
    """Locked local-panel window (universe local_panel, 0 bps)."""
    from finaince.baseline import run_locked_baseline

    typer.echo(json.dumps(run_locked_baseline(), default=str, ensure_ascii=False, indent=2))


@app.command("bench")
def bench_cmd(
    is_start: str = typer.Option("2019-01-01", "--is-start", help="In-sample window start"),
    is_end: str = typer.Option("2023-12-31", "--is-end"),
    oos_start: str = typer.Option("2024-01-01", "--oos-start"),
    oos_end: str = typer.Option("2024-12-31", "--oos-end"),
    cost_bps: float = typer.Option(5.0, "--cost-bps", help="Double-sided cost applied to quintile LS turnover"),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Live-fetch missing cache years + constituent snapshots via RQ (requires RQ_USER/RQ_PASS)",
    ),
    start_year: int = typer.Option(2019, "--start-year", help="--sync: first price year to fetch"),
    end_year: int = typer.Option(2024, "--end-year", help="--sync: last price year to fetch"),
) -> None:
    """WS-D citable CSI300 double-window benchmark over the point-in-time cache."""
    from finaince.data_track import run_bench, sync_cache

    if sync:
        outcome = sync_cache(start_year, end_year)
        if not outcome.get("ok"):
            typer.echo(json.dumps(outcome, ensure_ascii=False, indent=2), err=True)
            raise typer.Exit(code=1)
    result = run_bench(
        is_start=is_start,
        is_end=is_end,
        oos_start=oos_start,
        oos_end=oos_end,
        cost_bps=cost_bps,
    )
    typer.echo(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise typer.Exit(code=1)


@app.command("brain-submit")
def brain_submit_cmd(
    expression: str = typer.Argument(..., help="FASTEXPR expression to adjudicate"),
    catalog_id: Optional[str] = typer.Option(None, "--catalog-id", help="Write the ruling back onto this record"),
    region: str = typer.Option("CHN", "--region"),
    universe: str = typer.Option("TOP2000U", "--universe"),
) -> None:
    """WS-J external BRAIN ruling; degrades to the internal dual-window bench honestly."""
    from finaince.brain_track import adjudicate

    result = adjudicate(
        expression,
        catalog_id=catalog_id,
        settings={"region": region, "universe": universe},
    )
    typer.echo(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    if not result.get("ok") and result.get("adjudication_level") == "none":
        raise typer.Exit(code=1)


@app.command("campaign")
def campaign_cmd(
    root: Path = typer.Option(..., "--root", exists=True, file_okay=False, help="Directory of broker-report PDFs"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max reports to process this run"),
    stats_only: bool = typer.Option(False, "--stats", help="Print manifest statistics and exit"),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Requeue failed entries before running"),
) -> None:
    """WS-K corpus campaign: batch governed reproduction with resume."""
    from finaince import corpus_campaign

    if reset_failed:
        moved = corpus_campaign.reset_failed(root)
        typer.echo(json.dumps({"reset_failed": moved}, ensure_ascii=False))
    if stats_only:
        typer.echo(json.dumps(corpus_campaign.stats_summary(), ensure_ascii=False, indent=2))
        return
    outcome = corpus_campaign.run_campaign(root, limit=limit)
    typer.echo(json.dumps(outcome, default=str, ensure_ascii=False, indent=2))
    if not outcome.get("ok"):
        raise typer.Exit(code=1)


@app.command("loop")
def loop_cmd(
    steps: int = typer.Option(2, "--steps"),
    sync: bool = typer.Option(
        True,
        "--sync/--async",
        help="Record a platform job; --sync (default) runs the pipeline in-process",
    ),
    expression: list[str] = typer.Option(
        None,
        "--expression",
        help="Expressions to evaluate in factor steps (queue)",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        min=1,
        max=8,
        help="Parallel worker processes for batch expression evaluation",
    ),
) -> None:
    """Alternate a factor step and a model step toward a portfolio metric."""
    from finaince.jobs.runner import run_loop_job

    out = run_loop_job(steps=steps, sync=sync, expressions=expression, workers=workers)
    typer.echo(json.dumps(out, default=str, ensure_ascii=False))


@app.command("sdk-info")
def sdk_info_cmd() -> None:
    """Show Claude Agent SDK session options (custom MCP tools + hook)."""
    from finaince.sdk_ext import describe_session_options

    typer.echo(json.dumps(describe_session_options(), ensure_ascii=False, indent=2))


@app.command("agent")
def agent_cmd(
    prompt: str = typer.Argument(..., help="Research request for the FinAlpha desk"),
    max_turns: int = typer.Option(16, "--max-turns"),
) -> None:
    """Run the Claude Agent SDK research desk (tools + playbook + specialists)."""
    from finaince.agent import run_research_desk

    result = run_research_desk(prompt, max_turns=max_turns)
    typer.echo(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    if not result.get("ok"):
        raise typer.Exit(code=2)


@app.command("sdk-query")
def sdk_query_cmd(
    prompt: str = typer.Option(
        "List the custom finaince tools you have.",
        "--prompt",
        "-p",
    ),
) -> None:
    """Attempt a live Claude Agent SDK query() (needs claude CLI / credentials)."""
    from finaince.sdk_ext import try_live_query

    result = try_live_query(prompt)
    typer.echo(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    if not result.get("ok"):
        raise typer.Exit(code=2)


def main(args: list[str] | None = None) -> None:
    app(args=args)


if __name__ == "__main__":
    main()

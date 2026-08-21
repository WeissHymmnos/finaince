"""aiminer.api stand-ins so the workbench pages do not 404 on 3.12."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

_WIKILINK = re.compile(r"\[\[([A-Za-z0-9_\-]+)\]\]")


def _page(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    start = max(0, int(offset))
    cap = max(1, min(int(limit), 200))
    end = min(len(items), start + cap)
    return {
        "items": items[start:end],
        "total": len(items),
        "offset": start,
        "limit": cap,
        "next_offset": end,
    }


def _wiki_dirs() -> list[Path]:
    from finaince.runtime import documents_root
    from finaince.settings import get_settings

    settings = get_settings()
    return [
        settings.home / "aiminer" / "data" / "wiki_vault",
        settings.home / "data" / "wiki_vault",
        documents_root() / "aiminer" / "data" / "wiki_vault",
        documents_root() / "finaince" / "data" / "wiki_vault",
    ]


def _iter_wiki_pages() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in _wiki_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name in {"index.md", "log.md"}:
                continue
            slug = path.stem
            if slug in seen:
                continue
            seen.add(slug)
            text = path.read_text(encoding="utf-8", errors="replace")
            title = slug
            kind = "factor_card"
            status = "active"
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].splitlines():
                        if ":" not in line:
                            continue
                        key, value = line.split(":", 1)
                        key = key.strip().lower()
                        value = value.strip().strip("'\"")
                        if key == "title":
                            title = value or title
                        elif key == "type":
                            kind = value or kind
                        elif key == "status":
                            status = value or status
            rows.append(
                {
                    "slug": slug,
                    "title": title,
                    "updated": path.stat().st_mtime,
                    "type": kind,
                    "status": status,
                    "path": str(path),
                    "text": text,
                }
            )
    rows.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return rows


def _factor_items() -> list[dict[str, Any]]:
    from finaince.settings import get_settings

    settings = get_settings()
    rows: list[dict[str, Any]] = []
    try:
        from aiminer.pool_io import load_alpha_pool_rows

        for item in load_alpha_pool_rows(settings.aiminer_db):
            metrics = item.get("metrics") or {}
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "hypothesis": item.get("hypothesis") or item.get("name") or "",
                    "code": item.get("code") or item.get("expression") or "",
                    "run_id": str(item.get("run_id") or ""),
                    "iteration": int(item.get("iteration") or 0),
                    "ic": item.get("ic") or metrics.get("information_coefficient"),
                    "selection_score": item.get("selection_score"),
                    "best_strategy_id": item.get("best_strategy_id"),
                    "metrics": metrics,
                    "returns": item.get("returns") or {},
                    "daily_returns": item.get("returns") or {},
                }
            )
    except Exception:
        pass
    if rows:
        return rows
    from finaince.catalog.store import FactorCatalog

    for rec in FactorCatalog().list():
        rows.append(
            {
                "id": rec.id,
                "hypothesis": rec.name,
                "code": rec.expression.text,
                "run_id": rec.lineage.run_id or rec.lineage.source,
                "iteration": 0,
                "ic": rec.metrics.ic,
                "selection_score": rec.metrics.selection_score,
                "best_strategy_id": None,
                "metrics": {
                    "information_coefficient": rec.metrics.ic,
                    "sharpe": rec.metrics.sharpe,
                    "max_drawdown": rec.metrics.max_drawdown,
                    "annual_return": rec.metrics.annualized_return,
                },
                "returns": dict(rec.daily_returns or {}),
                "daily_returns": dict(rec.daily_returns or {}),
                "market_profile": rec.market_profile,
            }
        )
    return rows


def _job_to_run(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "pending")
    mapped = {
        "queued": "pending",
        "running": "running",
        "done": "completed",
        "error": "failed",
        "cancelled": "stopped",
    }.get(status, status)
    return {
        "run_id": job.get("engine_run_id") or job.get("id"),
        "status": mapped,
        "is_active": mapped in {"pending", "running", "starting"},
        "config": job.get("payload") or {},
        "started_at": job.get("created_at"),
        "ended_at": job.get("updated_at") if mapped in {"completed", "failed", "stopped"} else None,
        "result_counts": {"factor_count": 0, "strategy_count": 0},
        "error": job.get("error"),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _manual_dir(kind: str) -> Path:
    from finaince.settings import get_settings

    path = get_settings().home / "aiminer" / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _stable_id(*parts: Any) -> str:
    blob = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _backtest_path(job_id: str) -> Path:
    return _manual_dir("manual_backtests") / f"{job_id}.json"


def _strategy_path(strategy_id: str) -> Path:
    return _manual_dir("manual_strategies") / f"{strategy_id}.json"


def _list_json_dir(kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    folder = _manual_dir(kind)
    for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json(path)
        if payload:
            rows.append(payload)
    return rows


def _resolve_job(run_id: str) -> dict[str, Any] | None:
    from finaince.jobs.runner import get_job, list_jobs

    job = get_job(run_id)
    if job is not None:
        return job
    for row in list_jobs():
        if str(row.get("engine_run_id") or "") == run_id:
            return get_job(str(row["id"]))
    return None


def _swarm_extra(payload: dict[str, Any]) -> list[str]:
    extra: list[str] = []
    scalars = {
        "iterations": "--iterations",
        "llm_provider": "--llm-provider",
        "llm_model": "--llm-model",
        "llm_base_url": "--llm-base-url",
        "embedding_provider": "--embedding-provider",
        "local_data_path": "--local-data-path",
        "market_start": "--market-start",
        "market_end": "--market-end",
    }
    for key, flag in scalars.items():
        value = payload.get(key)
        if value not in (None, "", []):
            extra += [flag, str(value)]
    mode = str(payload.get("mode") or "")
    if mode in {"qlib", "ricequant"}:
        extra += ["--mode", mode]
    backend = str(payload.get("data_backend") or "")
    if backend in {"qlib", "ricequant", "local"}:
        extra += ["--data-backend", backend]
    engine = str(payload.get("engine") or "")
    if engine in {"pandas", "polars"}:
        extra += ["--engine", engine]
    effort = str(payload.get("llm_reasoning_effort") or "")
    if effort in {"low", "medium", "high", "xhigh"}:
        extra += ["--llm-reasoning-effort", effort]
    market_mode = str(payload.get("market_mode") or "")
    if market_mode == "multi":
        market_mode = "batch"
    if market_mode in {"single", "batch", "mixed"}:
        extra += ["--market-mode", market_mode]
    profile = str(payload.get("market_profile") or "")
    if profile in {"cn_stock", "us_stock", "futures"}:
        extra += ["--market-profile", profile]
    profiles = payload.get("market_profiles")
    if isinstance(profiles, list) and profiles:
        extra += ["--market-profiles", ",".join(str(item) for item in profiles)]
    elif isinstance(profiles, str) and profiles.strip():
        extra += ["--market-profiles", profiles.strip()]
    layout = str(payload.get("local_data_layout") or "")
    if layout in {"auto", "panel", "instrument_files"}:
        extra += ["--local-data-layout", layout]
    if payload.get("parallel"):
        extra.append("--parallel")
    roles = payload.get("roles")
    if isinstance(roles, list) and roles:
        extra += ["--roles", *[str(role) for role in roles]]
    elif isinstance(roles, str) and roles.strip():
        extra += ["--roles", *roles.split()]
    return extra


def _eval_payload(body: dict[str, Any]) -> dict[str, Any]:
    from finaince.eval.router import EvalRequest, evaluate
    from finaince.runtime import default_universe

    expression = str(body.get("expression") or "").strip()
    if not expression:
        raise HTTPException(400, "expression required")
    backend = str(body.get("data_backend") or "local").strip().lower() or "local"
    engine = str(body.get("engine") or "polars").strip().lower()
    dialect = "qlib" if backend == "qlib" or engine == "qlib" else "repro_polars"
    if dialect == "repro_polars" and "$" in expression:
        from finaince.eval.dialects import translate_from_qlib

        expression = translate_from_qlib(expression)
    universe = str(body.get("market") or body.get("universe") or "").strip() or default_universe(backend)
    start = body.get("start_date") or body.get("start")
    end = body.get("end_date") or body.get("end")
    try:
        result = evaluate(
            EvalRequest(
                expression=expression,
                dialect=dialect,
                data_backend=backend,
                universe=universe,
                start=str(start) if start else None,
                end=str(end) if end else None,
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    metrics = dict(result.metrics or {})
    daily = metrics.pop("daily_returns", None) or {}
    if not isinstance(daily, dict):
        daily = {}
    return {
        "ok": result.ok,
        "expression": expression,
        "dialect": result.dialect,
        "data_backend": result.data_backend,
        "engine": engine,
        "metrics": {
            "information_coefficient": metrics.get("ic_mean"),
            "sharpe": metrics.get("sharpe_ratio"),
            "max_drawdown": metrics.get("max_drawdown"),
            "annual_return": metrics.get("long_short_annual_return"),
            **metrics,
        },
        "daily_returns": {str(key): float(value) for key, value in daily.items() if _is_number(value)},
        "error": result.error,
        "warnings": result.warnings,
        "translatable": result.translatable,
        "alt_text": result.alt_text,
    }


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _wiki_write_path(slug: str) -> Path | None:
    for page in _iter_wiki_pages():
        if page["slug"] == slug:
            return Path(str(page["path"]))
    return None


def _wiki_lint(stale_days: int) -> dict[str, Any]:
    pages = _iter_wiki_pages()
    cutoff = time.time() - max(1, int(stale_days)) * 86400
    issues: list[dict[str, Any]] = []
    for page in pages:
        if not page.get("title"):
            issues.append({"slug": page["slug"], "kind": "missing_title"})
        if (page.get("updated") or 0) < cutoff:
            issues.append(
                {
                    "slug": page["slug"],
                    "kind": "stale",
                    "updated": page.get("updated"),
                }
            )
        text = str(page.get("text") or "")
        if not text.startswith("---"):
            issues.append({"slug": page["slug"], "kind": "missing_frontmatter"})
    return {"ok": not issues, "issues": issues, "checked": len(pages), "stale_days": stale_days}


def _reset_targets(scopes: list[str]) -> list[Path]:
    from finaince.settings import get_settings

    settings = get_settings()
    wanted = set(scopes)
    if "all" in wanted:
        wanted.update({"pool", "memory", "rag", "runs"})
    mapping = {
        "pool": [settings.aiminer_db],
        "memory": [
            settings.home / "aiminer" / "data" / "wiki_vault",
            settings.home / "data" / "wiki_vault",
        ],
        "rag": [
            settings.home / "aiminer" / "data" / "chroma",
            settings.home / "aiminer" / "data" / "rag",
        ],
        "runs": [
            settings.aiminer_results / "runs",
            settings.home / "aiminer" / "manual_backtests",
            settings.home / "aiminer" / "manual_strategies",
        ],
    }
    paths: list[Path] = []
    seen: set[Path] = set()
    for scope in ("pool", "memory", "rag", "runs"):
        if scope not in wanted:
            continue
        for path in mapping[scope]:
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _execute_reset(paths: list[Path]) -> list[dict[str, Any]]:
    from finaince.settings import get_settings

    trash = get_settings().aiminer_results / ".trash" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    trash.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        dest = trash / path.name
        suffix = 1
        while dest.exists():
            dest = trash / f"{path.name}.{suffix}"
            suffix += 1
        shutil.move(str(path), str(dest))
        moved.append({"src": str(path), "dest": str(dest)})
    return moved


def register_aiminer_fallbacks(app: FastAPI, *, reason: str) -> None:
    note = f"aiminer.api unavailable: {reason}"

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from finaince.jobs.runner import list_jobs

        active = [j.get("id") for j in list_jobs() if j.get("status") in {"running", "queued"}]
        return {
            "ok": False,
            "degraded": True,
            "status": "degraded",
            "auth_disabled": True,
            "error": note,
            "active_run_ids": active,
            "readiness": {"ok": False, "reason": note},
        }

    @app.get("/api/readiness")
    def readiness() -> dict[str, Any]:
        return {"ok": False, "degraded": True, "error": note}

    @app.get("/api/swarm/status")
    def swarm_status() -> dict[str, Any]:
        from finaince.jobs.runner import list_jobs

        active = [j for j in list_jobs() if j.get("status") in {"running", "queued"}]
        return {
            "running_count": len(active),
            "active_run_ids": [j.get("id") for j in active],
            "max_concurrent": 2,
            "error": note,
        }

    @app.get("/api/swarm/runs")
    def swarm_runs(offset: int = 0, limit: int = 50) -> dict[str, Any]:
        from finaince.jobs.runner import get_job, list_jobs

        items = []
        for row in list_jobs():
            if row.get("kind") not in {"discover_swarm", "reproduce_report"}:
                continue
            items.append(_job_to_run(get_job(str(row["id"]), reap=False) or row))
        page = _page(items, offset, limit)
        page["error"] = note
        return page

    @app.get("/api/swarm/runs/{run_id}")
    def swarm_run(run_id: str) -> dict[str, Any]:
        job = _resolve_job(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        return _job_to_run(job)

    @app.get("/api/swarm/runs/{run_id}/logs")
    def swarm_logs(run_id: str, offset: int = 0, limit: int = 100, tail: bool = False) -> dict[str, Any]:
        job = _resolve_job(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        entries: list[dict[str, Any]] = []
        if job.get("error"):
            entries.append(
                {
                    "type": "status",
                    "event": "error",
                    "level": "error",
                    "message": job.get("error"),
                    "timestamp": job.get("updated_at") or job.get("created_at"),
                }
            )
        result = job.get("result")
        if result is not None:
            entries.append(
                {
                    "type": "status",
                    "event": "result",
                    "level": "info",
                    "message": json.dumps(result, default=str)[:4000],
                    "timestamp": job.get("updated_at") or job.get("created_at"),
                }
            )
        if not entries:
            entries.append(
                {
                    "type": "status",
                    "event": job.get("status") or "pending",
                    "level": "info",
                    "message": f"job {job.get('id')} {job.get('status')}",
                    "timestamp": job.get("created_at"),
                }
            )
        if tail:
            start = max(0, len(entries) - max(1, int(limit)))
            return _page(entries, start, limit)
        return _page(entries, offset, limit)

    @app.post("/api/swarm/runs")
    def start_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from finaince.jobs.runner import run_swarm_job

        payload = body or {}
        job = run_swarm_job(_swarm_extra(payload), sync=False)
        return {"status": "starting", "run_id": job.get("id")}

    @app.post("/api/swarm/runs/{run_id}/stop")
    def stop_run(run_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import cancel

        job = _resolve_job(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        result = cancel(str(job["id"]))
        status = str((result.get("job") or job).get("status") or "")
        mapped = {
            "queued": "pending",
            "running": "stopping",
            "done": "completed",
            "error": "failed",
            "cancelled": "stopped",
        }.get(status, status or "stopped")
        if result.get("ok"):
            return {"status": "stopped", "run_id": job.get("id")}
        if mapped in {"completed", "failed", "stopped"}:
            return {"status": mapped, "run_id": job.get("id")}
        raise HTTPException(409, str(result.get("error") or "cannot stop run"))

    @app.delete("/api/swarm/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import delete_job

        job = _resolve_job(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        if job.get("status") in {"running", "queued"}:
            raise HTTPException(409, "run is still active; stop it first")
        if not delete_job(str(job["id"])):
            raise HTTPException(404, "run not found")
        return {"status": "deleted", "run_id": job.get("id")}

    @app.get("/api/results")
    def results(
        run_id: str | None = Query(default=None),
        offset: int = Query(default=0),
        limit: int = Query(default=50),
    ) -> dict[str, Any]:
        items = _factor_items()
        if run_id:
            items = [i for i in items if str(i.get("run_id") or "") == run_id]
        return _page(items, offset, limit)

    @app.get("/api/factors/{factor_id}")
    def factor_detail(factor_id: str) -> dict[str, Any]:
        for item in _factor_items():
            if item.get("id") == factor_id:
                return item
        raise HTTPException(404, "factor not found")

    @app.post("/api/factors/crossover")
    def crossover() -> dict[str, Any]:
        return {"status": "rejected", "reason": note, "ok": False}

    @app.get("/api/wiki/index")
    def wiki_index(offset: int = 0, limit: int = 50) -> dict[str, Any]:
        rows = [
            {k: page[k] for k in ("slug", "title", "updated", "type", "status")}
            for page in _iter_wiki_pages()
        ]
        return _page(rows, offset, limit)

    @app.get("/api/wiki/page/{slug}")
    def wiki_page(slug: str) -> PlainTextResponse:
        for page in _iter_wiki_pages():
            if page["slug"] == slug:
                return PlainTextResponse(page["text"], media_type="text/markdown; charset=utf-8")
        raise HTTPException(404, "wiki page not found")

    @app.get("/api/wiki/graph")
    def wiki_graph() -> dict[str, Any]:
        pages = _iter_wiki_pages()
        nodes = [
            {
                "id": page["slug"],
                "slug": page["slug"],
                "title": page["title"],
                "type": page.get("type") or "factor_card",
                "status": page.get("status") or "active",
            }
            for page in pages
        ]
        slugs = {n["slug"] for n in nodes}
        edges: list[dict[str, str]] = []
        for page in pages:
            for target in _WIKILINK.findall(page.get("text") or ""):
                if target in slugs:
                    edges.append({"source": page["slug"], "target": target, "kind": "wikilink"})
        return {"nodes": nodes, "edges": edges}

    @app.post("/api/wiki/lint")
    def wiki_lint(stale_days: int = 30) -> dict[str, Any]:
        payload = _wiki_lint(stale_days)
        payload["note"] = note
        return payload

    @app.put("/api/wiki/page/{slug}")
    def wiki_update(slug: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if slug in {"index", "log"}:
            raise HTTPException(400, "system wiki pages are read-only")
        content = str((body or {}).get("content") or "")
        if not content.strip():
            raise HTTPException(400, "content required")
        path = _wiki_write_path(slug)
        if path is None:
            raise HTTPException(404, "wiki page not found")
        normalized = content.rstrip() + "\n"
        path.write_text(normalized, encoding="utf-8")
        return {"status": "saved", "slug": slug, "bytes": len(normalized.encode("utf-8"))}

    @app.post("/api/wiki/migrate")
    def wiki_migrate(dry_run: bool = True) -> dict[str, Any]:
        return {"ok": True, "moved": 0, "dry_run": dry_run, "note": note}

    @app.get("/api/strategies")
    def strategies(
        run_id: str | None = Query(default=None),
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        items = _list_json_dir("manual_strategies")
        if run_id:
            items = [item for item in items if str(item.get("run_id") or "") == run_id]
        return _page(items, offset, limit)

    @app.get("/api/strategies/{strategy_id}")
    def strategy_detail(strategy_id: str) -> dict[str, Any]:
        payload = _read_json(_strategy_path(strategy_id))
        if payload is None:
            raise HTTPException(404, "strategy not found")
        return payload

    @app.post("/api/strategy/run")
    def strategy_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(body or {})
        evaluated = _eval_payload(payload)
        config = payload.get("strategy_config") if isinstance(payload.get("strategy_config"), dict) else {}
        strategy_id = _stable_id(
            evaluated["expression"],
            json.dumps(config, sort_keys=True, default=str),
            payload.get("data_backend"),
            payload.get("market_profile"),
            _now_iso(),
        )
        record = {
            **evaluated,
            "strategy_id": strategy_id,
            "run_id": payload.get("run_id") or "manual",
            "label": (config or {}).get("label") or "Web strategy backtest",
            "template_name": (config or {}).get("template_name") or (config or {}).get("strategy_mode"),
            "strategy_config": config,
            "strategy_mode": (config or {}).get("strategy_mode") or "cross_sectional",
            "source_factor_id": payload.get("source_factor_id"),
            "ran_at": _now_iso(),
            "is_primary": True,
            "candidate_rank": 1,
            "selection_score": (evaluated.get("metrics") or {}).get("information_coefficient"),
            "market_profile": payload.get("market_profile"),
            "market_mode": payload.get("market_mode"),
        }
        _write_json(_strategy_path(strategy_id), record)
        return record

    @app.get("/api/strategy/history")
    def strategy_history() -> list[dict[str, Any]]:
        rows = []
        for item in _list_json_dir("manual_strategies"):
            slim = dict(item)
            slim.pop("daily_returns", None)
            slim["return_points"] = len(item.get("daily_returns") or {})
            rows.append(slim)
        return rows

    @app.delete("/api/strategy/{strategy_id}")
    def strategy_delete(strategy_id: str) -> dict[str, Any]:
        path = _strategy_path(strategy_id)
        if not path.exists():
            raise HTTPException(404, "strategy backtest not found")
        path.unlink()
        return {"status": "deleted", "strategy_id": strategy_id}

    @app.post("/api/backtest/validate")
    def backtest_validate(body: dict[str, Any] | None = None) -> dict[str, Any]:
        expression = str((body or {}).get("expression") or "").strip()
        if not expression:
            return {"ok": False, "message": "expression required"}
        from reproagent.reproducer.polars_engine import validate_expression

        from finaince.eval.dialects import translate_from_qlib

        candidates = [expression]
        translated = translate_from_qlib(expression)
        if translated != expression:
            candidates.append(translated)
        last_message = "invalid expression"
        for candidate in candidates:
            try:
                payload = validate_expression(candidate)
            except Exception as exc:  # noqa: BLE001
                last_message = str(exc)
                continue
            if payload.get("valid"):
                message = "ok"
                if candidate != expression:
                    message = f"ok (translated qlib fields to {candidate})"
                return {"ok": True, "message": message}
            last_message = ";".join(str(item) for item in (payload.get("errors") or [])) or last_message
        return {"ok": False, "message": last_message}

    @app.post("/api/backtest/run")
    def backtest_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(body or {})
        job_id = _stable_id(
            payload.get("expression"),
            payload.get("start_date"),
            payload.get("end_date"),
            payload.get("engine"),
            payload.get("market"),
            payload.get("data_backend"),
            payload.get("market_profile"),
            payload.get("local_data_path"),
        )
        cached = _read_json(_backtest_path(job_id))
        if cached:
            cached["cached"] = True
            return cached
        evaluated = _eval_payload(payload)
        record = {
            **evaluated,
            "job_id": job_id,
            "label": payload.get("label") or "Web manual backtest",
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "market": payload.get("market"),
            "market_profile": payload.get("market_profile"),
            "market_mode": payload.get("market_mode"),
            "ran_at": _now_iso(),
            "return_points": len(evaluated.get("daily_returns") or {}),
            "cached": False,
        }
        if not record["ok"]:
            raise HTTPException(400, str(record.get("error") or "backtest failed"))
        _write_json(_backtest_path(job_id), record)
        return record

    @app.get("/api/backtest/history")
    def backtest_history() -> list[dict[str, Any]]:
        rows = []
        for item in _list_json_dir("manual_backtests"):
            slim = dict(item)
            slim.pop("daily_returns", None)
            slim["return_points"] = item.get("return_points") or len(item.get("daily_returns") or {})
            rows.append(slim)
        return rows

    @app.get("/api/backtest/{job_id}")
    def backtest_get(job_id: str) -> dict[str, Any]:
        payload = _read_json(_backtest_path(job_id))
        if payload is None:
            raise HTTPException(404, "backtest job not found")
        return payload

    @app.delete("/api/backtest/{job_id}")
    def backtest_delete(job_id: str) -> dict[str, Any]:
        path = _backtest_path(job_id)
        if not path.exists():
            raise HTTPException(404, "backtest job not found")
        path.unlink()
        return {"status": "deleted", "job_id": job_id}

    @app.get("/api/reports/{factor_id}")
    def factor_report(factor_id: str) -> PlainTextResponse:
        from finaince.settings import get_settings

        path = get_settings().aiminer_results / "reports" / f"{factor_id}.md"
        if not path.is_file():
            raise HTTPException(404, "report not found")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.post("/api/admin/reset")
    def admin_reset(body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = body or {}
        scopes = list(payload.get("scopes") or ["pool"])
        allowed = {"pool", "memory", "rag", "runs", "all"}
        unknown = [scope for scope in scopes if scope not in allowed]
        if unknown:
            raise HTTPException(400, f"unknown scopes: {unknown}")
        confirm = bool(payload.get("confirm"))
        if confirm:
            expected = os.getenv("AIMINER_RESET_TOKEN") or os.getenv("FINAINCE_RESET_TOKEN")
            if not expected:
                raise HTTPException(503, "AIMINER_RESET_TOKEN is not configured on the server")
            if str(payload.get("reset_token") or "") != expected:
                raise HTTPException(403, "reset_token mismatch")
        paths = _reset_targets(scopes)
        plan = [{"path": str(path), "exists": path.exists()} for path in paths]
        plan_text = (
            "\n".join(f"{'move' if item['exists'] else 'skip'} {item['path']}" for item in plan)
            or "(nothing to move)"
        )
        moved = _execute_reset(paths) if confirm else []
        return {
            "scopes": scopes,
            "confirm": confirm,
            "moved": moved,
            "plan": plan,
            "plan_text": plan_text,
            "note": note,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        from finaince.jobs.runner import list_jobs

        active = [j.get("id") for j in list_jobs() if j.get("status") in {"running", "queued"}]
        await websocket.send_json(
            {
                "type": "status",
                "event": "connected",
                "status": "ok",
                "active_run_ids": active,
                "max_concurrent": 2,
            }
        )
        try:
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            return

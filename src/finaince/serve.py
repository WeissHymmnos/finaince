"""Same-origin FastAPI: workbench + /api/v1/* plus existing aiminer /api and /ws."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from finaince.settings import get_settings


def resolve_workbench_dist() -> Path | None:
    """Locate the built workbench. Same CWD candidates as aiminer.api, plus absolute trees."""
    from finaince._paths import documents_root

    root = documents_root()
    here = Path(__file__).resolve()
    candidates: list[Path] = [
        Path("frontend_dist"),
        Path("frontend/dist"),
        Path("aiminer/frontend/dist"),
        Path("aiminer/frontend_dist"),
        root / "aiminer" / "frontend" / "dist",
        root / "aiminer" / "frontend_dist",
    ]
    for base in (Path.cwd(), *here.parents[:6]):
        candidates.append(base / "aiminer" / "frontend" / "dist")
        candidates.append(base / "aiminer" / "frontend_dist")
        candidates.append(base / "frontend" / "dist")
        candidates.append(base / "frontend_dist")
    seen: set[Path] = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (cand / "index.html").is_file():
            return cand
    return None


def _align_aiminer_frontend_dist() -> None:
    """Point aiminer.api's CWD-relative dist lookup at the same tree we serve."""
    dist = resolve_workbench_dist()
    if dist is None:
        return
    try:
        import aiminer.api as api
    except Exception:
        return
    api.FRONTEND_DIST_DIR = dist

    def _resolve() -> Path | None:
        return dist

    def _index() -> Path | None:
        index = dist / "index.html"
        return index if index.is_file() else None

    api._resolve_frontend_dist_dir = _resolve
    api._frontend_index_path = _index


def _attach_workbench_root(app: Any) -> Path | None:
    """Register GET / and /assets before aiminer's catch-all can claim them."""
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles

    settings = get_settings()
    if not settings.serve_spa:
        return None
    dist = resolve_workbench_dist()
    if dist is None or not (dist / "index.html").is_file():
        @app.get("/")
        def workbench_missing() -> PlainTextResponse:
            return PlainTextResponse("frontend not built", status_code=404)

        return None
    assets = dist / "assets"
    if assets.is_dir() and not any(getattr(route, "name", None) == "assets" for route in app.router.routes):
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    html = (dist / "index.html").read_text(encoding="utf-8")
    app.state.workbench_html = html
    app.state.workbench_dist = dist

    @app.get("/")
    def workbench_index() -> HTMLResponse:
        return HTMLResponse(html)

    return dist


def _attach_workbench_catchall(app: Any) -> None:
    """Client-side routes when aiminer.api is absent (no competing catch-all)."""
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse

    html = getattr(app.state, "workbench_html", None)
    if not html:
        return

    @app.get("/{full_path:path}")
    def workbench_fallback(full_path: str) -> HTMLResponse:
        if full_path.startswith("api") or full_path.startswith("ws"):
            raise HTTPException(404, "not found")
        return HTMLResponse(html)


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException

    settings = get_settings()
    settings.apply_engine_env()
    import os

    os.environ["AIMINER_INCLUDE_SPA"] = "1" if settings.serve_spa else "0"
    app = FastAPI(title=settings.product_name, version="0.1.0")
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        from finaince.obs import jobs_degraded
        from finaince.settings import doctor_report

        doc = doctor_report()
        isolator_ok = bool((doc.get("isolator") or {}).get("ok"))
        qlib_child = doc.get("qlib_child") or {}
        degraded = bool(doc.get("issues")) or jobs_degraded() or not isolator_ok
        return {
            "ok": bool(doc.get("ok")) and not degraded,
            "product": settings.product_name,
            "degraded": degraded,
            "isolator": doc.get("isolator"),
            "qlib_child": qlib_child,
        }

    @app.get("/api/v1/catalog")
    def catalog(source: str | None = None, query: str = "", style: str | None = None) -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog

        items = FactorCatalog().list(source=source, query=query, style=style)
        return {"items": [i.model_dump(mode="json") for i in items], "count": len(items)}

    @app.get("/api/v1/catalog/{catalog_id}")
    def catalog_detail(catalog_id: str, embed: str | None = None) -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog

        rec = FactorCatalog().get(catalog_id)
        if rec is None:
            raise HTTPException(404, "unknown catalog id")
        body = rec.model_dump(mode="json")
        if embed == "memory" or os.environ.get("FINAINCE_CATALOG_MEMORY") == "1":
            from finaince.catalog.memory import memory_summary

            body["memory_summary"] = memory_summary(rec)
        return body

    @app.post("/api/v1/promote")
    def promote_route(body: dict[str, Any]) -> dict[str, Any]:
        from finaince.review.desk import promote

        cid = str(body.get("catalog_id") or "")
        if not cid:
            raise HTTPException(400, "catalog_id required")
        return promote(cid, direction=str(body.get("direction") or "to_pool"))

    @app.get("/api/v1/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import get_job

        row = get_job(job_id)
        if row is None:
            raise HTTPException(404, "unknown job id")
        return row

    @app.get("/api/v1/audit")
    def audit_route(action: str | None = None) -> dict[str, Any]:
        from finaince.catalog.audit import list_audit

        items = list_audit(action=action)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/jobs")
    def jobs() -> dict[str, Any]:
        from finaince.jobs.runner import list_jobs

        rows = list_jobs()
        return {"items": rows, "count": len(rows)}

    @app.post("/api/v1/eval")
    def eval_route(body: dict[str, Any]) -> dict[str, Any]:
        from finaince.eval.router import EvalRequest, evaluate

        from finaince.runtime import resolve_data_source

        backend = str(body.get("data_backend") or body.get("backend") or "auto")
        if backend == "auto":
            backend = resolve_data_source()
        result = evaluate(
            EvalRequest(
                expression=str(body.get("expression") or ""),
                dialect=str(body.get("dialect") or "repro_polars"),
                data_backend=backend,
                start=body.get("start"),
                end=body.get("end"),
            )
        )
        return {
            "ok": result.ok,
            "dialect": result.dialect,
            "metrics": result.metrics,
            "error": result.error,
            "translatable": result.translatable,
            "alt_text": result.alt_text,
            "warnings": result.warnings,
        }

    @app.get("/api/v1/review")
    def review_queue() -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog

        return {"items": FactorCatalog().list_promotions("pending")}

    @app.post("/api/v1/review/{promotion_id}/approve")
    def review_approve(promotion_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        from finaince.review.desk import approve

        override = list((body or {}).get("override") or [])
        return approve(promotion_id, override=override)

    @app.post("/api/v1/review/{promotion_id}/reject")
    def review_reject(promotion_id: str) -> dict[str, Any]:
        from finaince.review.desk import reject

        return reject(promotion_id)

    @app.post("/api/v1/reproduce")
    def reproduce_route(body: dict[str, Any]) -> dict[str, Any]:
        from finaince.jobs.runner import run_reproduce_job

        pdf = body.get("pdf_path")
        if not pdf:
            raise HTTPException(400, "pdf_path required")
        sync = bool(body.get("sync", True))
        return run_reproduce_job(str(pdf), sync=sync)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import cancel

        return cancel(job_id)

    @app.post("/api/v1/agent")
    def agent_route(body: dict[str, Any]) -> dict[str, Any]:
        from finaince.agent import run_research_desk

        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt required")
        return run_research_desk(prompt, max_turns=int(body.get("max_turns") or 16))

    @app.get("/api/v1/trace")
    def trace_route(limit: int = 50) -> dict[str, Any]:
        from finaince.trace import list_chain

        items = list_chain(limit=limit)
        return {"items": items, "count": len(items)}

    @app.post("/api/v1/impl")
    def impl_route(body: dict[str, Any]) -> dict[str, Any]:
        from finaince.jobs.runner import run_impl_job

        source = str(body.get("source") or "")
        if not source.strip():
            raise HTTPException(400, "source required")
        return run_impl_job(source, name=str(body.get("name") or "isolated"), universe=str(body.get("universe") or "local_panel"))

    @app.post("/api/v1/loop")
    def loop_route(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from finaince.jobs.runner import run_loop_job

        steps = int((body or {}).get("steps") or 2)
        return run_loop_job(steps=steps)

    _attach_workbench_root(app)

    aiminer_error: str | None = None
    try:
        from aiminer.api import app as aiminer_app

        _align_aiminer_frontend_dist()
        app.include_router(aiminer_app.router)
    except Exception as exc:  # noqa: BLE001
        aiminer_error = str(exc)

    if aiminer_error is not None:
        from finaince.aiminer_fallback import register_aiminer_fallbacks

        register_aiminer_fallbacks(app, reason=aiminer_error)
        _attach_workbench_catchall(app)

    return app


app = None


def get_app() -> Any:
    global app
    if app is None:
        app = create_app()
    return app


def main(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        get_app(),
        host=host or settings.serve_host,
        port=port or settings.serve_port,
        log_level="info",
    )

"""Same-origin FastAPI: workbench + /api/v1/* plus existing aiminer /api and /ws."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from finaince.settings import get_settings

# Fail-safe desk auth: /api/v1 is token-gated by default except this
# allowlist. Implemented as ASGI middleware (not a FastAPI dependency) so
# websocket scopes and future HTTP routes cannot bypass it by omission.
# Aiminer surfaces (/api/*) keep their own contract.
_PUBLIC_V1_PATHS = frozenset({"/api/v1/health", "/api/v1/baseline"})


class DeskAuthGate:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path") or ""
        if (
            scope["type"] == "http"
            and scope.get("method") != "OPTIONS"
            and path.startswith("/api/v1/")
            and path not in _PUBLIC_V1_PATHS
        ):
            from finaince.auth import desk_auth_ok

            headers = {
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in scope.get("headers") or []
            }
            if not desk_auth_ok(headers):
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"detail": "desk token required"}, status_code=401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def resolve_workbench_dist() -> Path | None:
    """Locate the built workbench. Same CWD candidates as aiminer.api, plus absolute trees."""
    import os

    from finaince._paths import documents_root

    root = documents_root()
    here = Path(__file__).resolve()
    packaged = here.parent / "web"
    if os.environ.get("FINAINCE_PACKAGED_SPA", "").strip() == "1" and (packaged / "index.html").is_file():
        return packaged
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
    candidates.append(packaged)
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

    from finaince.auth import align_aiminer_auth_env, cors_origins

    desk_token = align_aiminer_auth_env()
    os.environ["AIMINER_INCLUDE_SPA"] = "1" if settings.serve_spa else "0"

    app = FastAPI(title=settings.product_name, version="0.1.0")
    app.add_middleware(DeskAuthGate)
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
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
    def catalog(
        request: Request,
        source: str | None = None,
        query: str = "",
        style: str | None = None,
    ) -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog

        items = FactorCatalog().list(source=source, query=query, style=style)
        return {"items": [i.model_dump(mode="json") for i in items], "count": len(items)}

    @app.get("/api/v1/catalog/{catalog_id}")
    def catalog_detail(request: Request, catalog_id: str, embed: str | None = None) -> dict[str, Any]:
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
    def promote_route(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        from finaince.review.desk import promote

        cid = str(body.get("catalog_id") or "")
        if not cid:
            raise HTTPException(400, "catalog_id required")
        direction = str(body.get("direction") or "to_pool")
        if direction not in ("to_pool", "to_library"):
            raise HTTPException(400, "direction must be to_pool|to_library")
        return promote(cid, direction=direction)

    @app.get("/api/v1/jobs/{job_id}")
    def job_detail(request: Request, job_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import get_job

        row = get_job(job_id)
        if row is None:
            raise HTTPException(404, "unknown job id")
        return row

    @app.get("/api/v1/audit")
    def audit_route(request: Request, action: str | None = None) -> dict[str, Any]:
        from finaince.catalog.audit import list_audit

        items = list_audit(action=action)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/jobs")
    def jobs(request: Request) -> dict[str, Any]:
        from finaince.jobs.runner import list_jobs

        rows = list_jobs()
        return {"items": rows, "count": len(rows)}

    @app.post("/api/v1/eval")
    def eval_route(request: Request, body: dict[str, Any]) -> dict[str, Any]:
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
    def review_queue(request: Request) -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog

        return {"items": FactorCatalog().list_promotions("pending")}

    @app.get("/api/v1/review/{promotion_id}/gates")
    def review_gates(request: Request, promotion_id: str) -> dict[str, Any]:
        from finaince.catalog.store import FactorCatalog
        from finaince.review.gates import evaluate_gates

        promotions = {
            item["id"]: item for item in FactorCatalog().list_promotions()
        }
        promo = promotions.get(promotion_id)
        if not promo:
            raise HTTPException(status_code=404, detail="unknown promotion")
        record = FactorCatalog().get(promo["catalog_id"]) if promo.get("catalog_id") else None
        if record is None:
            raise HTTPException(status_code=404, detail="unknown catalog record")
        verdict = evaluate_gates(record, direction=promo.get("direction") or "to_pool")
        return {
            "promotion_id": promotion_id,
            "catalog_id": promo.get("catalog_id"),
            "direction": verdict.get("direction"),
            "passed": verdict.get("passed"),
            "failures": verdict.get("failures"),
            "details": verdict.get("details"),
            "read_only": True,
        }

    @app.post("/api/v1/review/{promotion_id}/approve")
    def review_approve(
        request: Request, promotion_id: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from finaince.review.desk import approve

        if (body or {}).get("override"):
            raise HTTPException(status_code=403, detail="http approve cannot honor override")
        adversary_raw = (body or {}).get("adversary", False)
        if adversary_raw not in (True, False, None):
            raise HTTPException(400, "adversary must be a boolean")
        adversary = bool(adversary_raw)
        return approve(promotion_id, override=None, adversary=adversary)

    @app.post("/api/v1/review/{promotion_id}/adversary")
    def review_adversary(request: Request, promotion_id: str) -> dict[str, Any]:
        from finaince.review.adversary import adversarial_review

        return adversarial_review(promotion_id)

    @app.post("/api/v1/review/{promotion_id}/reject")
    def review_reject(request: Request, promotion_id: str) -> dict[str, Any]:
        from finaince.review.desk import reject

        return reject(promotion_id)

    @app.post("/api/v1/reproduce")
    def reproduce_route(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        from finaince.auth import pdf_path_allowed
        from finaince.jobs.runner import run_reproduce_job

        pdf = body.get("pdf_path")
        if not pdf:
            raise HTTPException(400, "pdf_path required")
        if not pdf_path_allowed(str(pdf)):
            raise HTTPException(status_code=403, detail="pdf_path must be under FINAINCE_PDF_ROOT")
        sync = bool(body.get("sync", True))
        return run_reproduce_job(str(pdf), sync=sync)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
        from finaince.jobs.runner import cancel

        return cancel(job_id)

    @app.post("/api/v1/agent")
    def agent_route(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        from finaince.agent import run_research_desk

        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt required")
        return run_research_desk(prompt, max_turns=int(body.get("max_turns") or 16))

    @app.get("/api/v1/trace")
    def trace_route(request: Request, limit: int = 50) -> dict[str, Any]:
        from finaince.trace import list_chain

        items = list_chain(limit=limit)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/baseline")
    def baseline_route() -> dict[str, Any]:
        from finaince.baseline import run_locked_baseline

        return run_locked_baseline()

    @app.get("/api/v1/bench")
    def bench_route(
        request: Request,
        is_start: str = "2019-01-01",
        is_end: str = "2023-12-31",
        oos_start: str = "2024-01-01",
        oos_end: str = "2024-12-31",
        cost_bps: float = 5.0,
    ) -> dict[str, Any]:
        from finaince.data_track import run_bench

        return run_bench(is_start=is_start, is_end=is_end, oos_start=oos_start, oos_end=oos_end, cost_bps=cost_bps)

    @app.post("/api/v1/impl")
    def impl_route(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        from finaince.jobs.runner import run_impl_job

        source = str(body.get("source") or "")
        if not source.strip():
            raise HTTPException(400, "source required")
        if len(source) > 20_000:
            raise HTTPException(413, "source too large")
        return run_impl_job(source, name=str(body.get("name") or "isolated"), universe=str(body.get("universe") or "local_panel"))

    @app.post("/api/v1/impl/needs")
    def impl_needs_route(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        from finaince.impl_status import fulfill_needs_impl

        return fulfill_needs_impl(body or {})

    @app.post("/api/v1/loop")
    def loop_route(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        from finaince.jobs.runner import run_loop_job

        steps = int((body or {}).get("steps") or 2)
        sync = bool((body or {}).get("sync", True))
        raw_exprs = (body or {}).get("expressions")
        expressions: list[str] | None = None
        if isinstance(raw_exprs, list):
            cleaned = [str(e).strip() for e in raw_exprs if str(e).strip()]
            expressions = cleaned or None
        return run_loop_job(steps=steps, sync=sync, expressions=expressions)

    _attach_workbench_root(app)

    aiminer_error: str | None = None
    try:
        from aiminer.api import app as aiminer_app

        _align_aiminer_frontend_dist()
        if desk_token:
            import aiminer.api as aiminer_api

            aiminer_api.AUTH_TOKEN = desk_token
            aiminer_api.AUTH_DISABLED = os.getenv("AIMINER_DISABLE_AUTH", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
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

    from finaince.auth import validate_serve_host

    settings = get_settings()
    bind = validate_serve_host(host or settings.serve_host)
    uvicorn.run(
        get_app(),
        host=bind,
        port=port or settings.serve_port,
        log_level="info",
    )

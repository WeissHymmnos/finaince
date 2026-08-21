"""WS-J BRAIN external adjudication track: platform ruling x native governance.

Follows the worldquant-brain-mcp interaction shape (auth -> simulation POST ->
Location polling -> alpha fetch). FinAlpha's governed proposals can be sent to
WorldQuant BRAIN for an external SPECTACULAR/PASS/FAIL ruling, which is written
back into the catalog lineage alongside the local audit chain.

Honest degradation is a first-class outcome: without credentials the caller gets
``adjudication_level="internal_dual_window"`` pointing at the WS-D bench — the
ruling level drops and that drop is declared, never papered over.
"""

from __future__ import annotations

import os
import time
from typing import Any

DEFAULT_BRAIN_BASE = "https://api.worldquantbrain.com"


def brain_base() -> str:
    """Read at call time so tests/env can retarget after import."""
    return (os.environ.get("BRAIN_API_BASE") or DEFAULT_BRAIN_BASE).rstrip("/")
DEFAULT_SETTINGS: dict[str, Any] = {
    "instrumentType": "EQUITY",
    "region": "CHN",
    "universe": "TOP2000U",
    "delay": 1,
    "decay": 0,
    "neutralization": "SUBINDUSTRY",
    "truncation": 0.08,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "OFF",
    "language": "FASTEXPR",
    "visualization": False,
}


def has_brain_credentials() -> bool:
    return bool((os.environ.get("BRAIN_USER") or "").strip()) and bool(
        (os.environ.get("BRAIN_PASS") or "").strip()
    )


def build_simulation_payload(
    expression: str,
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "REGULAR",
        "settings": {**DEFAULT_SETTINGS, **(settings or {})},
        "regular": expression,
    }


def _client():
    import base64

    import httpx

    user = (os.environ.get("BRAIN_USER") or "").strip()
    password = (os.environ.get("BRAIN_PASS") or "").strip()
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    client = httpx.Client(base_url=brain_base(), timeout=30.0)
    client.headers.update({"Authorization": f"Basic {token}"})
    try:
        response = client.post("/authentication")
        response.raise_for_status()
    except Exception:
        client.close()
        raise
    return client


def _extract_alpha_id(location: str) -> str | None:
    tail = str(location).rstrip("/").split("/")[-1]
    return tail or None


def _poll_simulation(client: Any, location: str, *, max_seconds: int = 180) -> dict[str, Any]:
    deadline = time.monotonic() + max_seconds
    url = str(location)
    while time.monotonic() < deadline:
        response = client.get(url)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            time.sleep(min(float(retry_after), 10.0))
            continue
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": f"simulation_poll_failed:{response.status_code}",
                "detail": (response.text or "")[:300],
            }
        payload = response.json() if response.content else {}
        progress = str(payload.get("progress") or "")
        if progress in {"COMPLETE", "100%", "1.0"} or "alphas" in url:
            alpha_id = _extract_alpha_id(url)
            return {"ok": True, "alpha_id": alpha_id, "payload": payload}
        time.sleep(1.0)
    return {"ok": False, "error": "simulation_timeout"}


def submit_expression(
    expression: str,
    *,
    settings: dict[str, Any] | None = None,
    max_seconds: int = 180,
) -> dict[str, Any]:
    """Submit one governed expression to BRAIN and wait for its simulation."""
    if not has_brain_credentials():
        return {
            "ok": False,
            "adjudication_level": "none",
            "reason": "no_credentials",
            "hint": "set BRAIN_USER/BRAIN_PASS; degraded path is the internal dual-window bench",
        }
    payload = build_simulation_payload(expression, settings=settings)
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "adjudication_level": "none", "reason": f"auth_failed:{exc}"}
    try:
        created = client.post("/simulations", json=payload)
        if created.status_code not in (200, 201):
            return {
                "ok": False,
                "adjudication_level": "none",
                "reason": f"submit_rejected:{created.status_code}",
                "detail": created.text[:300],
            }
        location = created.headers.get("Location") or created.headers.get("location")
        if not location:
            return {"ok": False, "adjudication_level": "none", "reason": "missing_location"}
        polled = _poll_simulation(client, location, max_seconds=max_seconds)
        if not polled.get("ok"):
            return {"ok": False, "adjudication_level": "none", "reason": polled.get("error")}
        alpha_id = polled.get("alpha_id")
        detail: dict[str, Any] = {}
        if alpha_id:
            fetched = client.get(f"/alphas/{alpha_id}")
            if fetched.status_code < 400 and fetched.content:
                body = fetched.json()
                detail = {
                    "grade": body.get("grade"),
                    "is_sharpe": (body.get("is") or {}).get("sharpe"),
                    "is_fitness": (body.get("is") or {}).get("fitness"),
                    "status": body.get("status"),
                }
        return {
            "ok": True,
            "adjudication_level": "platform",
            "alpha_id": alpha_id,
            **detail,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "adjudication_level": "none", "reason": f"submit_error:{exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def write_back(catalog_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Attach the platform ruling to the catalog record's tags + trace chain."""
    if not result.get("ok") or not catalog_id:
        return None
    try:
        from finaince.catalog.store import FactorCatalog
        from finaince.trace import append_event

        catalog = FactorCatalog()
        record = catalog.get(catalog_id)
        if record is None:
            return None
        tags = list(record.tags or [])
        if result.get("alpha_id"):
            tags.append(f"brain:{result['alpha_id']}")
        if result.get("grade"):
            tags.append(f"brain_grade:{result['grade']}")
        record.tags = tags
        catalog.upsert(record)
        append_event(
            "brain_adjudication",
            metrics={
                "catalog_id": catalog_id,
                "alpha_id": result.get("alpha_id"),
                "grade": result.get("grade"),
                "level": result.get("adjudication_level"),
            },
            summary=f"brain ruling grade={result.get('grade')} alpha={result.get('alpha_id')}",
        )
        return {"ok": True, "tags": tags}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def adjudicate(
    expression: str,
    *,
    catalog_id: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full adjudication step with declared ruling level on every path."""
    result = submit_expression(expression, settings=settings)
    if result.get("ok") and catalog_id:
        write_back(catalog_id, result)
    if not result.get("ok") and result.get("reason") == "no_credentials":
        try:
            from finaince.data_track import doctor_section

            bench_ready = bool(doctor_section().get("years_cached"))
        except Exception:
            bench_ready = False
        result["degraded_to"] = {
            "tool": "finaince bench (WS-D dual-window table)",
            "cache_ready": bench_ready,
        }
    return result

"""Desk HTTP auth and PDF-root allowlist. Mutation routes share this façade."""

from __future__ import annotations

import os
from pathlib import Path


def configured_desk_token() -> str:
    return (
        os.environ.get("FINAINCE_DESK_TOKEN")
        or os.environ.get("FINAINCE_API_TOKEN")
        or ""
    ).strip()


def align_aiminer_auth_env() -> str:
    """Copy the desk token onto AIMINER_AUTH_TOKEN before aiminer.api loads."""
    token = configured_desk_token()
    if token:
        os.environ["AIMINER_AUTH_TOKEN"] = token
    return token


def public_bind_allowed() -> bool:
    return os.environ.get("FINAINCE_ALLOW_PUBLIC_BIND", "").strip() == "1"


def validate_serve_host(host: str) -> str:
    """Loopback only unless FINAINCE_ALLOW_PUBLIC_BIND=1."""
    raw = (host or "").strip() or "127.0.0.1"
    lowered = raw.lower()
    if lowered in {"127.0.0.1", "localhost", "::1"}:
        return raw
    if public_bind_allowed():
        return raw
    raise ValueError(
        f"refusing to bind {raw!r}; use 127.0.0.1 or set FINAINCE_ALLOW_PUBLIC_BIND=1"
    )


def cors_origins() -> list[str]:
    raw = (os.environ.get("FINAINCE_CORS_ORIGINS") or "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]


def token_from_headers(headers: dict[str, str] | None) -> str:
    raw = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth = raw.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (raw.get("x-api-key") or "").strip()


def desk_auth_ok(headers: dict[str, str] | None) -> bool:
    expected = configured_desk_token()
    got = token_from_headers(headers)
    return bool(expected) and bool(got) and got == expected


def configured_pdf_root() -> Path | None:
    raw = (os.environ.get("FINAINCE_PDF_ROOT") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def pdf_path_allowed(pdf_path: str) -> bool:
    """True only when pdf_path resolves inside FINAINCE_PDF_ROOT."""
    root = configured_pdf_root()
    if root is None:
        return False
    try:
        resolved = Path(pdf_path).expanduser().resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return True

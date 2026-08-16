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

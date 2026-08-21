"""WS-K sell-side research corpus campaign: batch governed reproduction with resume.

Overnight batch over a directory of broker-report PDFs. Every report goes
through the full reproduce -> catalog -> governance flow; ``no_factors`` is an
honest terminal state (a report with no extractable factor is recorded as such,
never retried into existence, never padded). The manifest on disk makes the
campaign resumable across runs.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1
TERMINAL_STATES = {"done", "no_factors", "skipped_missing"}


def campaign_dir() -> Path:
    from finaince.settings import get_settings

    path = get_settings().home / "corpus_campaign"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path() -> Path:
    return campaign_dir() / "manifest.json"


def load_manifest() -> dict[str, Any]:
    path = _manifest_path()
    if not path.exists():
        return {"schema_version": MANIFEST_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload.get("entries"), dict):
            raise ValueError("bad entries")
        return payload
    except (json.JSONDecodeError, ValueError):
        backup = path.with_suffix(".corrupt")
        path.replace(backup)
        return {"schema_version": MANIFEST_VERSION, "entries": {}}


def save_manifest(manifest: dict[str, Any]) -> None:
    _atomic_write_json(_manifest_path(), manifest)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """tmp + os.replace: a crash never leaves a half-written JSON behind."""
    tmp = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    os.replace(tmp, target)


def scan_corpus(root: str | Path) -> list[Path]:
    directory = Path(root)
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.pdf"))


def classify_outcome(result: dict[str, Any] | None) -> tuple[str, list[str], str | None]:
    """Map one reproduction result to (state, catalog_ids, error)."""
    if result is None:
        return ("failed", [], "empty_result")
    status = str(result.get("status") or "")
    factors = result.get("factors") or []
    error = result.get("error") or result.get("reason")
    if status == "no_factors" or (status in {"", "ok"} and not factors):
        return ("no_factors", [], None if status == "no_factors" else f"no_factors_from:{status or 'unknown'}")
    if isinstance(error, str) and error.strip():
        return ("failed", [], error[:300])
    catalog_ids = []
    for factor in factors:
        cid = factor.get("catalog_id") or factor.get("id")
        if cid:
            catalog_ids.append(str(cid))
    if not catalog_ids:
        return ("no_factors", [], None)
    return ("done", catalog_ids, None)


def process_one(pdf_path: str | Path, *, backtest_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    from finaince.reproduction import reproduce_report

    try:
        raw = reproduce_report(Path(pdf_path), backtest_kwargs=backtest_kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "catalog_ids": [], "error": str(exc)[:300]}
    state, catalog_ids, error = classify_outcome(raw)
    return {"state": state, "catalog_ids": catalog_ids, "error": error}


def run_campaign(
    root: str | Path,
    *,
    limit: int | None = None,
    backtest_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process every pending PDF under ``root``; terminal entries are skipped."""
    manifest = load_manifest()
    entries: dict[str, dict[str, Any]] = manifest["entries"]
    pdfs = scan_corpus(root)
    processed = skipped = 0
    results: list[dict[str, Any]] = []
    for pdf in pdfs:
        key = str(pdf.resolve())
        record = entries.get(key) or {}
        if record.get("status") in TERMINAL_STATES - {"skipped_missing"}:
            skipped += 1
            continue
        if limit is not None and processed >= limit:
            break
        outcome = process_one(key, backtest_kwargs=backtest_kwargs)
        entries[key] = {
            "status": outcome["state"],
            "attempts": int(record.get("attempts") or 0) + 1,
            "catalog_ids": outcome["catalog_ids"],
            "last_error": outcome["error"],
            "updated_at": datetime.now(UTC).isoformat(),
        }
        save_manifest(manifest)
        processed += 1
        results.append({"pdf": pdf.name, **outcome})
    missing = [
        key
        for key in entries
        if not Path(key).exists() and entries[key].get("status") != "skipped_missing"
    ]
    for key in missing:
        entries[key]["status"] = "skipped_missing"
        entries[key]["updated_at"] = datetime.now(UTC).isoformat()
    save_manifest(manifest)
    stats = stats_summary(manifest)
    return {
        "ok": True,
        "root": str(root),
        "discovered": len(pdfs),
        "processed": processed,
        "already_terminal": skipped,
        "stats": stats,
        "results": results,
    }


def stats_summary(manifest: dict[str, Any] | None = None) -> dict[str, int]:
    manifest = manifest or load_manifest()
    counts = {state: 0 for state in ("pending", "done", "no_factors", "failed", "skipped_missing")}
    for record in manifest["entries"].values():
        state = str(record.get("status") or "pending")
        counts[state] = counts.get(state, 0) + 1
    counts["total_known"] = sum(counts.values())
    counts["factors_cataloged"] = sum(
        len(record.get("catalog_ids") or []) for record in manifest["entries"].values()
    )
    return counts


def reset_failed(root: str | Path | None = None) -> int:
    """Move failed entries back to pending so the next run retries them."""
    manifest = load_manifest()
    moved = 0
    for key, record in manifest["entries"].items():
        if record.get("status") != "failed":
            continue
        if root is not None and not key.startswith(str(Path(root).resolve())):
            continue
        record["status"] = "pending"
        moved += 1
    save_manifest(manifest)
    return moved

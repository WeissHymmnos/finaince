"""WS-L governance-grounded process memory (AlphaMemo, superseded).

Credit assignment uses the strongest outcome signal this platform owns —
whether an edit survived the fail-closed gates and adversary review — instead
of raw residual regression. The unit of memory is an AST-diff edit motive
(what changed between two drafts) tagged by the error class it was reacting to.

Also hosts the experience-chain display layer (former WS-B): return-correlated
factor chains where a factor may only extend a chain's tail when its RankIC
beats every member.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MEMORY_VERSION = 1
WEIGHT_FLOOR = -10.0
WEIGHT_CEIL = 10.0


def _root() -> Path:
    from finaince.settings import get_settings

    path = get_settings().home / "process_memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_path() -> Path:
    return _root() / "memory.json"


def _chains_path() -> Path:
    return _root() / "chains.json"


def load_memory() -> dict[str, Any]:
    path = _memory_path()
    if not path.exists():
        return {"schema_version": MEMORY_VERSION, "motifs": {}}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        backup = path.with_suffix(".corrupt")
        path.replace(backup)
        return {"schema_version": MEMORY_VERSION, "motifs": {}}
    payload.setdefault("schema_version", MEMORY_VERSION)
    payload.setdefault("motifs", {})
    return payload


def save_memory(payload: dict[str, Any]) -> None:
    _atomic_write_json(_memory_path(), payload)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    import os

    tmp = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    os.replace(tmp, target)


def record_edit_outcome(
    *,
    error_prefix: str,
    motif: dict[str, Any],
    survived_gates: bool,
) -> dict[str, Any]:
    """Confidence-weighted credit: gate/adversary survival is the label."""
    if not isinstance(motif, dict):
        raise TypeError("motif must be a dict")
    signature = _motif_signature(error_prefix, motif)
    payload = load_memory()
    entry = payload["motifs"].setdefault(
        signature,
        {
            "weight": 0.0,
            "attempts": 0,
            "survived": 0,
            "error_prefix": error_prefix[:80],
            "motif": {k: motif.get(k) for k in ("added_lines", "removed_lines", "changed_regions", "signatures_equal")},
            "updated_at": None,
        },
    )
    entry["attempts"] += 1
    delta = 2.0 if survived_gates else -1.0
    entry["weight"] = max(WEIGHT_FLOOR, min(WEIGHT_CEIL, entry["weight"] + delta))
    if survived_gates:
        entry["survived"] += 1
    entry["updated_at"] = datetime.now(UTC).isoformat()
    save_memory(payload)
    return {"ok": True, "signature": signature, "weight": entry["weight"]}


def _motif_signature(error_prefix: str, motif: dict[str, Any]) -> str:
    raw = f"{(error_prefix or 'none').split(':', 1)[0]}|{sorted((k, v) for k, v in motif.items())}"
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def recall_motifs(*, error_prefix: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Top-weighted motifs for prompt injection; optional error-class filter."""
    payload = load_memory()
    entries = []
    for signature, item in payload["motifs"].items():
        if error_prefix and (item.get("error_prefix") or "").split(":", 1)[0] != error_prefix.split(":", 1)[0]:
            continue
        entries.append({"signature": signature, **item})
    entries.sort(key=lambda e: e["weight"], reverse=True)
    return entries[: max(1, int(limit))]


def context_block(*, error_prefix: str | None = None, limit: int = 3) -> str:
    """Render memory as advisor-prompt lines; empty string when nothing learned."""
    motifs = recall_motifs(error_prefix=error_prefix, limit=limit)
    lines = []
    for motif in motifs:
        detail = motif.get("motif") or {}
        lines.append(
            "process memory: after "
            f"{motif.get('error_prefix')} adding {detail.get('added_lines', '?')}"
            f"/removing {detail.get('removed_lines', '?')} lines survived gates "
            f"{motif.get('survived')}/{motif.get('attempts')} times"
        )
    return "\n".join(lines)


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 10 or n != len(b):
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=False))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def load_chains() -> dict[str, Any]:
    path = _chains_path()
    if not path.exists():
        return {"schema_version": MEMORY_VERSION, "chains": []}
    try:
        payload = json.loads(path.read_text())
        payload.setdefault("chains", [])
        return payload
    except json.JSONDecodeError:
        backup = path.with_suffix(".corrupt")
        path.replace(backup)
        return {"schema_version": MEMORY_VERSION, "chains": []}


def save_chains(payload: dict[str, Any]) -> None:
    _atomic_write_json(_chains_path(), payload)


def update_chains(record_id: str, returns: dict[str, float], rank_ic: float | None, *, corr_threshold: float = 0.7) -> dict[str, Any]:
    """FAMA-style chain update: compare against the highest-correlated member.

    Extension still happens only at the tail, and only when the candidate's
    RankIC beats every member; a match at an interior member with a weaker
    RankIC is rejected rather than split (split semantics reserved).
    """
    store = load_chains()
    chains: list[list[dict[str, Any]]] = [
        [member for member in chain] for chain in store["chains"]
    ]
    decision = {"record_id": record_id, "action": "new_chain"}
    best_corr = None
    best_chain_index = None
    best_member_position: int | None = None
    for index, chain in enumerate(chains):
        for position, member in enumerate(chain):
            member_returns = member.get("returns") or {}
            common = sorted(set(member_returns) & set(returns))
            corr = _pearson(
                [float(member_returns[d]) for d in common],
                [float(returns[d]) for d in common],
            )
            if corr is not None and abs(corr) >= corr_threshold:
                if best_corr is None or abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_chain_index = index
                    best_member_position = position
    if best_chain_index is not None:
        decision["best_member_position"] = best_member_position
        chain_members = chains[best_chain_index]
        beats_all = rank_ic is not None and all(
            rank_ic > float(member.get("rank_ic") or -math.inf) for member in chain_members
        )
        if beats_all:
            chain_members.append(
                {
                    "record_id": record_id,
                    "returns": dict(sorted(returns.items())),
                    "rank_ic": rank_ic,
                }
            )
            decision["action"] = "extend"
            decision["chain"] = best_chain_index
        else:
            decision["action"] = "reject_not_beating_chain"
            decision["chain"] = best_chain_index
    else:
        chains.append(
            [{"record_id": record_id, "returns": dict(sorted(returns.items())), "rank_ic": rank_ic}]
        )
    save_chains({"schema_version": MEMORY_VERSION, "chains": chains})
    decision["n_chains"] = len(chains)
    return decision


def chains_display(limit: int = 5) -> list[dict[str, Any]]:
    store = load_chains()
    out = []
    for index, chain in enumerate(store["chains"][-limit:]):
        out.append(
            {
                "chain": index,
                "length": len(chain),
                "head": chain[0].get("record_id") if chain else None,
                "tail": chain[-1].get("record_id") if chain else None,
                "best_rank_ic": max((m.get("rank_ic") or 0.0) for m in chain) if chain else None,
            }
        )
    return out

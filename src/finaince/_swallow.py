"""Observable replacement for ``except Exception: pass`` ([P2-22] / ISSUE-R4-04).

Best-effort side paths (audit, tracing, enrichment) must not break the main
flow, but a swallowed failure must leave a trace so gate/trial-count
degradations are diagnosable.
"""

from __future__ import annotations

import logging

_LOG = logging.getLogger("finaince.swallowed")


def log_swallowed(where: str, exc: BaseException) -> None:
    _LOG.warning("swallowed %s at %s: %s", type(exc).__name__, where, str(exc)[:200])

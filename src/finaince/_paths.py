"""Make sibling aiminer and reproagent source trees importable."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def documents_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "aiminer" / "src").is_dir() and (parent / "reproagent" / "src").is_dir():
            return parent
    return here.parents[3]


_DOCS = documents_root()
_AIMINER_SRC = _DOCS / "aiminer" / "src"
_REPRO_SRC = _DOCS / "reproagent" / "src"
_FINPDFPRO_SRC = _DOCS / "finpdfpro" / "src"


def path_hack_disabled() -> bool:
    """Sibling src injection is off unless the operator opts in.

    ``FINAINCE_PATH_HACK=1`` enables the author-tree fallback.
    ``FINAINCE_NO_PATH_HACK=1`` keeps the historical kill switch (still off).
    """
    if os.environ.get("FINAINCE_NO_PATH_HACK", "").strip() == "1":
        return True
    if os.environ.get("FINAINCE_PATH_HACK", "").strip() == "1":
        return False
    return True


def ensure_import_paths() -> list[str]:
    """Insert sibling package src dirs at the front of sys.path.

    ``finpdfpro/src`` is forced first so ``finreportparser`` is 0.5.x, not the
    0.2.0 copy vendored under reproagent/src.

    Set ``FINAINCE_NO_PATH_HACK=1`` to skip injection (installed extras only).

    Returns ``[aiminer, reproagent, finpdfpro]`` (existing-or-not) so callers
    can keep using index 0 as aiminer.
    """
    if path_hack_disabled():
        return []
    ensured: list[str] = []
    for src in (_AIMINER_SRC, _REPRO_SRC):
        path = str(src)
        ensured.append(path)
        if src.is_dir() and path not in sys.path:
            sys.path.insert(0, path)
    if _FINPDFPRO_SRC.is_dir():
        path = str(_FINPDFPRO_SRC)
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
        ensured.append(path)
    return ensured

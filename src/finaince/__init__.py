"""finaince — merged aiminer discovery + reproagent reproduction surface."""

from __future__ import annotations

from finaince._paths import ensure_import_paths
from finaince._reproagent_compat import ensure_reproagent_compat

ensure_import_paths()
ensure_reproagent_compat()

__version__ = "0.1.0"

__all__ = ["__version__"]

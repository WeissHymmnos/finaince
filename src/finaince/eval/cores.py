"""Which evaluator produced a number, and whether that number is citable.

Three cores exist. None of them writes the catalog. Candidates are written by
``loop._catalog_candidate`` and ``isolate.upsert_isolated``. The cores do not
share a ledger. Missing pit cache is a fact reported here; it does not by
itself fail ``finaince doctor``.
"""

from __future__ import annotations

from typing import Any


def evaluation_cores() -> dict[str, Any]:
    """Describe pit_core, the reproagent bundle, and the qlib child."""
    pit_years: list[int] = []
    pit_manifest = False
    pit_error: str | None = None
    try:
        from finaince.data_track import doctor_section

        section = doctor_section()
        pit_years = list(section.get("years_cached") or [])
        pit_manifest = bool(section.get("manifest_present"))
    except Exception as exc:  # noqa: BLE001
        pit_error = f"{type(exc).__name__}: {exc}"[:200]

    pit_ready = bool(pit_years) and pit_manifest and pit_error is None

    bundle_error: str | None = None
    try:
        from reproagent.reproducer.backtest_bundle import build_backtest_bundle  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        bundle_error = f"{type(exc).__name__}: {exc}"[:200]
    bundle_ready = bundle_error is None

    qlib_error: str | None = None
    try:
        from aiminer.core.qlib_child import run_request  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        qlib_error = f"{type(exc).__name__}: {exc}"[:200]

    # eval and bench print metrics. These two call sites are the catalog writers.
    catalog_writers = ["loop._catalog_candidate", "isolate.upsert_isolated"]
    return {
        "citable_engine": "pit_core" if pit_ready else None,
        "catalog_engine": None,
        "catalog_writers": catalog_writers,
        "same_ledger": False,
        "cores": {
            "pit_core": {
                "ready": pit_ready,
                "citable": pit_ready,
                "writes_catalog": False,
                "entry": "finaince bench",
                "years_cached": pit_years,
                "error": pit_error,
            },
            "engine_native": {
                "ready": bundle_ready,
                "citable": False,
                "writes_catalog": False,
                "entry": "finaince eval --dialect repro_polars",
                "error": bundle_error,
            },
            "qlib": {
                "ready": qlib_error is None,
                "citable": False,
                "writes_catalog": False,
                "entry": "finaince eval --dialect qlib",
                "error": qlib_error,
            },
        },
    }

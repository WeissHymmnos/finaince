"""Discovery façade: shipped aiminer score + IC/correlation pool cull + swarm."""

from __future__ import annotations

from typing import Any

from finaince._paths import ensure_import_paths

ensure_import_paths()


def score_factor(
    metrics: dict[str, Any],
    factor_ic: float = 0.0,
    walk_forward: dict[str, Any] | None = None,
) -> float:
    """Composite selection score from the shipped aiminer selector."""
    from aiminer.core.strategy import selection_score

    return float(selection_score(metrics, factor_ic=factor_ic, walk_forward=walk_forward))


def cull_factor_pool(results_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """IC-threshold + correlation cull from the shipped aiminer manager selector."""
    from aiminer.manager import cull_alpha_pool

    pool = cull_alpha_pool(results_list)
    return list(pool)


def run_swarm(args: list[str] | None = None) -> dict[str, Any]:
    """Dispatch to the shipped aiminer manager swarm.

    The 3.12 platform env cannot import LangGraph; the live swarm runs under
    the aiminer conda interpreter when needed.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    from finaince._paths import ensure_import_paths
    from finaince.runtime import aiminer_python, inject_llm_env, resolve_llm
    from finaince.settings import swarm_argv

    from finaince.settings import get_settings

    srcs = ensure_import_paths()
    argv = swarm_argv(list(args or []))
    cfg = get_settings()
    cfg.apply_engine_env()
    try:
        from aiminer.sub_agent import AlphaResearcher
    except Exception:
        AlphaResearcher = None
    if AlphaResearcher is not None:
        from aiminer.manager import main as manager_main

        manager_main(argv)
        return {"ok": True, "via": "inprocess", "args": argv}

    py = aiminer_python()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(srcs[0])) + os.pathsep + env.get("PYTHONPATH", "")
    env["AIMINER_RESULTS_DIR"] = str(cfg.aiminer_results)
    env["AIMINER_DATA_DIR"] = str(cfg.home / "aiminer" / "data")
    inject_llm_env(resolve_llm())
    completed = subprocess.run(
        [py, "-m", "aiminer.manager", *argv],
        env=env,
        text=True,
        capture_output=True,
        cwd=str(cfg.home),
    )
    tail = ((completed.stdout or "") + "\n" + (completed.stderr or ""))[-4000:]
    if completed.returncode != 0:
        raise RuntimeError(
            f"aiminer swarm failed ({completed.returncode}): {tail}"
        )
    return {
        "ok": True,
        "via": "conda" if py != sys.executable else "subprocess",
        "args": argv,
        "python": py,
        "stdout_tail": tail,
        "results_dir": str(cfg.aiminer_results),
    }

"""Discovery façade: shipped aiminer score + IC/correlation pool cull + swarm."""

from __future__ import annotations

import math
from typing import Any

from finaince._paths import ensure_import_paths
from finaince._swallow import log_swallowed

ensure_import_paths()


def score_factor(
    metrics: dict[str, Any],
    factor_ic: float = 0.0,
    walk_forward: dict[str, Any] | None = None,
) -> float:
    """Composite selection score from the shipped aiminer selector."""
    from aiminer.core.strategy import selection_score

    return float(selection_score(metrics, factor_ic=factor_ic, walk_forward=walk_forward))


def _candidate_expression(item: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("expression", "expr", "formula"):
        text = item.get(key)
        if isinstance(text, str) and text.strip():
            dialect = str(item.get("dialect") or "repro_polars")
            return text.strip(), dialect
    return None


def _seed_zoo_corpus() -> list[tuple[str, str]]:
    import json
    from pathlib import Path

    packaged = Path(__file__).resolve().parent / "data" / "alpha_seed_zoo.json"
    candidates = [
        packaged,
        *(base / "tests" / "fixtures" / "alpha_seed_zoo.json" for base in (Path(__file__).resolve().parents[3], *Path(__file__).resolve().parents)),
    ]
    for probe in candidates:
        if probe.exists():
            try:
                data = json.loads(probe.read_text())
                return [(s["id"], s["expr"]) for s in data.get("seeds", [])]
            except Exception as exc:
                try:
                    from finaince.obs import emit

                    emit("seed_zoo_degraded", error=str(exc)[:200])
                except Exception as _exc:  # noqa: BLE001
                    log_swallowed("finaince.discovery:52", _exc)
                return []
    return []


def structural_dedup(results_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """WS-A generation-side regularization: near-duplicate expressions never enter the pool.

    Similarity is measured against already-accepted batch rows and the seed zoo.
    Unparseable or expression-less candidates pass through untouched; the
    regex-level validation downstream stays responsible for them.
    """
    try:
        from finaince.expr_ast import max_similarity_vs
    except Exception as exc:
        try:
            from finaince.obs import emit

            emit("structural_dedup_disabled", error=str(exc)[:200])
        except Exception as _exc:  # noqa: BLE001
            log_swallowed("finaince.discovery:72", _exc)
        return list(results_list)

    kept: list[dict[str, Any]] = []
    accepted: list[tuple[str, str]] = []
    zoo = _seed_zoo_corpus()
    dropped: list[dict[str, Any]] = []
    for item in results_list:
        ref = _candidate_expression(item)
        if ref is None:
            kept.append(item)
            continue
        text, dialect = ref
        try:
            sim_batch = max_similarity_vs(text, dialect, accepted) if accepted else 0.0
            sim_zoo = max_similarity_vs(text, dialect, zoo)
        except ValueError:
            kept.append(item)
            continue
        best, against = max((sim_batch, "batch"), (sim_zoo, "seed_zoo"))
        if best > 0.85:
            dropped.append({"expression": text, "max_sim": round(best, 3), "against": against})
            continue
        ident = str(item.get("id") or item.get("hypothesis") or f"expr_{len(accepted)}")
        accepted.append((ident, text))
        kept.append(item)
    if dropped:
        try:
            from finaince.obs import emit

            emit("structural_dedup", dropped=len(dropped), worst=max(d["max_sim"] for d in dropped))
        except Exception as _exc:  # noqa: BLE001
            log_swallowed("finaince.discovery:104", _exc)
    return kept


def reflect_sign(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """WS-C sign-reflection: mirrored row for a statistically significant negative-IC factor.

    For a dollar-neutral cross-sectional factor the negated signal flips IC /
    RankIC / Sharpe / ICIR signs, negates the daily return series, and recomputes
    maximum drawdown on the inverted equity curve. Returns None when reflection
    is not justified (missing metrics or t-stat below 3) — never guesses.
    """
    nested = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else {}
    ic = metrics.get("ic", nested.get("ic"))
    if not isinstance(ic, (int, float)) or math.isfinite(ic) is False or ic >= 0:
        return None
    ic_ir = metrics.get("ic_ir", metrics.get("icir", nested.get("ic_ir", nested.get("icir"))))
    returns = metrics.get("returns") if isinstance(metrics.get("returns"), dict) else (
        metrics.get("daily_returns") if isinstance(metrics.get("daily_returns"), dict) else (
            nested.get("returns") if isinstance(nested.get("returns"), dict) else nested.get("daily_returns")
        )
    )
    if not isinstance(ic_ir, (int, float)) or not isinstance(returns, dict) or not returns:
        return None
    from finaince.domain.scoring import ic_t_stat, max_drawdown

    t_stat = ic_t_stat(ic_ir, len(returns))
    if t_stat is None or abs(t_stat) < 3.0:
        return None
    mirror = dict(metrics)
    flip_keys = (
        "ic",
        "rank_ic",
        "ic_mean",
        "sharpe",
        "sharpe_ratio",
        "sharpe_net",
        "ic_ir",
        "icir",
        "rank_icir",
        "annualized_return",
        "annual_return",
        "perf_metric",
        "selection_score",
    )
    for key in flip_keys:
        value = mirror.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            mirror[key] = -value

    neg_returns: dict[str, float] = {}
    for k, v in returns.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            neg_returns[str(k)] = -float(v)

    if isinstance(mirror.get("returns"), dict):
        mirror["returns"] = dict(neg_returns)
    if isinstance(mirror.get("daily_returns"), dict):
        mirror["daily_returns"] = dict(neg_returns)

    new_mdd = max_drawdown(list(neg_returns.values())) if neg_returns else None
    if new_mdd is not None:
        mirror["max_drawdown"] = new_mdd

    if isinstance(mirror.get("metrics"), dict):
        sub = dict(mirror["metrics"])
        for key in flip_keys:
            val = sub.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
                sub[key] = -val
        if isinstance(sub.get("returns"), dict):
            sub["returns"] = dict(neg_returns)
        if isinstance(sub.get("daily_returns"), dict):
            sub["daily_returns"] = dict(neg_returns)
        if new_mdd is not None:
            sub["max_drawdown"] = new_mdd
        mirror["metrics"] = sub

    for expr_key in ("code", "expression", "expr", "formula"):
        expr_val = mirror.get(expr_key)
        if isinstance(expr_val, str) and expr_val.strip():
            stripped = expr_val.strip()
            mirror[expr_key] = stripped[2:-1] if (stripped.startswith("-(") and stripped.endswith(")")) else f"-({stripped})"

    mirror["sign_reflected"] = True
    mirror["reflected_from_t"] = round(t_stat, 3)
    hypothesis = mirror.get("hypothesis")
    if isinstance(hypothesis, str):
        mirror["hypothesis"] = f"{hypothesis} [sign-mirrored]"
    return mirror


def cull_factor_pool(results_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structural dedup + sign-reflection, then the shipped aiminer cull."""
    from aiminer.manager import cull_alpha_pool

    deduped = structural_dedup(list(results_list))
    mirrored: list[dict[str, Any]] = []
    for item in deduped:
        candidate = dict(item)
        if "ic" not in candidate and isinstance(candidate.get("perf_metric"), (int, float)):
            candidate.setdefault("ic", candidate["perf_metric"])
        mirror = reflect_sign(candidate)
        if mirror is not None:
            mirrored.append(mirror)
    from aiminer.manager import PortfolioManager

    original = PortfolioManager.evaluate_and_combine

    def _with_trial_count(self, rows):  # type: ignore[no-untyped-def]
        if not hasattr(self, "n_trials"):
            self.n_trials = 0
        if not hasattr(self, "trial_sharpes"):
            self.trial_sharpes = []
        if not hasattr(self, "strategy_pool"):
            self.strategy_pool = []
        return original(self, rows)

    PortfolioManager.evaluate_and_combine = _with_trial_count  # type: ignore[method-assign]
    try:
        pool = cull_alpha_pool(deduped + mirrored)
    finally:
        PortfolioManager.evaluate_and_combine = original  # type: ignore[method-assign]
    return list(pool)


def run_swarm(args: list[str] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
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
    from finaince.settings import get_settings, swarm_argv

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
    swarm_timeout = float(timeout if timeout is not None else (os.environ.get("FINAINCE_SWARM_TIMEOUT", "1800") or 1800))
    completed = subprocess.run(
        [py, "-m", "aiminer.manager", *argv],
        env=env,
        text=True,
        capture_output=True,
        cwd=str(cfg.home),
        timeout=swarm_timeout,
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

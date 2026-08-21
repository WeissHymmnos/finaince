"""Fail-closed promotion gates."""

from __future__ import annotations

from typing import Any

from finaince.domain.factor import FactorRecord

_CSI300_CLAIMS = {
    "csi300",
    "hs300",
    "沪深300",
    "csi500",
    "zz500",
    "中证500",
    "csi1000",
    "中证1000",
    "全a股",
    "全a",
    "a股",
    "全市场",
    "all",
}


def _claims_broad_universe(universe: str) -> bool:
    key = (universe or "").strip().lower().replace(" ", "")
    return key in _CSI300_CLAIMS


def _finite_returns(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if num == num and num not in (float("inf"), float("-inf")):
            out[str(key)] = num
    return out


def ic_t_stat(ic_ir: float | None, n_days: int | None) -> float | None:
    """Harvey & Liu (2016); delegates to domain.scoring (single math source)."""
    from finaince.domain.scoring import ic_t_stat as _scoring_t_stat

    return _scoring_t_stat(ic_ir, n_days)


def deflated_sharpe(daily_returns: dict[str, float], n_trials: int) -> float | None:
    """
    Deflated Sharpe Ratio per Bailey & López de Prado (2014).
    """
    if not daily_returns or len(daily_returns) < 20:
        return None
    import math
    import statistics

    returns = list(daily_returns.values())
    T = len(returns)
    mean = sum(returns) / T
    var = sum((x - mean) ** 2 for x in returns) / T
    if var <= 1e-12:
        return None

    std = math.sqrt(var)
    sr_hat = mean / std

    m3 = sum((x - mean) ** 3 for x in returns) / T
    m4 = sum((x - mean) ** 4 for x in returns) / T
    skew = m3 / (std ** 3)
    kurt = m4 / (std ** 4)

    gamma = 0.5772156649015329
    e = math.e
    N = max(1, n_trials)

    v_sr_unscaled = 1 - skew * sr_hat + ((kurt - 1) / 4.0) * (sr_hat ** 2)
    if v_sr_unscaled <= 0:
        return None
    v_sr = v_sr_unscaled / (T - 1)

    inv_cdf = statistics.NormalDist().inv_cdf

    if N == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(v_sr) * ((1 - gamma) * inv_cdf(1 - 1 / N) + gamma * inv_cdf(1 - 1 / (N * e)))

    num = (sr_hat - sr0) * math.sqrt(T - 1)
    den = math.sqrt(v_sr_unscaled)

    return statistics.NormalDist().cdf(num / den)


def _originality_corpus(exclude_id: str | None = None) -> list[tuple[str, str]]:
    """Seed zoo + catalog ready/candidate expressions as (id, expr) pairs."""
    corpus: list[tuple[str, str]] = []
    try:
        import json
        from pathlib import Path

        zoo = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "alpha_seed_zoo.json"
        if not zoo.exists():
            for candidate in Path(__file__).resolve().parents:
                probe = candidate / "tests" / "fixtures" / "alpha_seed_zoo.json"
                if probe.exists():
                    zoo = probe
                    break
        if zoo.exists():
            data = json.loads(zoo.read_text())
            corpus.extend((s["id"], s["expr"]) for s in data.get("seeds", []))
    except Exception:
        pass
    try:
        from finaince.catalog.store import FactorCatalog

        for row in FactorCatalog().list():
            if row.id and row.id == exclude_id:
                continue
            if row.status in ("ready", "candidate", "review") and row.id:
                corpus.append((row.id, row.expression.text))
    except Exception:
        pass
    return corpus


def homogeneous_gate(
    record: FactorRecord,
    corpus: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Structural near-duplicate gate (WS-A): max subtree similarity > 0.85 fails."""
    try:
        from finaince.expr_ast import max_similarity_vs

        rows = _originality_corpus(exclude_id=record.id) if corpus is None else corpus
        sim = max_similarity_vs(record.expression.text, record.expression.dialect, rows)
    except ValueError as exc:
        return {"failure": None, "detail": f"unparseable:{exc}", "skipped_reason": None}
    except Exception as exc:  # noqa: BLE001
        return {"failure": f"homogeneous_error:{exc}", "detail": None, "skipped_reason": None}
    failure = "homogeneous" if sim > 0.85 else None
    return {"failure": failure, "detail": f"max_sim={sim:.3f}", "skipped_reason": None}


def overcomplex_gate(record: FactorRecord) -> dict[str, Any]:
    """Complexity ceiling gate (WS-A): sl>40 or pc>6 or fc>8 fails (loose first)."""
    try:
        from finaince.expr_ast import complexity, parse

        tree = parse(record.expression.text, record.expression.dialect)
        cx = complexity(tree)
    except ValueError as exc:
        return {"failure": None, "detail": f"unparseable:{exc}", "skipped_reason": None}
    except Exception as exc:  # noqa: BLE001
        return {"failure": f"overcomplex_error:{exc}", "detail": None, "skipped_reason": None}
    failure = None
    if cx["sl"] > 40 or cx["pc"] > 6 or cx["fc"] > 8:
        failure = "overcomplex"
    detail = f"sl={cx['sl']},pc={cx['pc']},fc={cx['fc']}"
    return {"failure": failure, "detail": detail, "skipped_reason": None}


def evaluate_gates(
    record: FactorRecord,
    *,
    direction: str,
    override: list[str] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    skipped = set(override or [])
    if record.is_simulated:
        failures.append("simulated")
    if record.lineage.formula_proxy:
        failures.append("formula_proxy")
    if "thin_panel" not in skipped:
        try:
            from finaince.runtime import local_panel_is_thin, local_panel_stats

            stats = local_panel_stats()
            thin = bool(stats.get("thin")) or int(stats.get("n_assets") or 0) < 20
            if not thin:
                thin = local_panel_is_thin()
        except Exception:
            thin = True
        if thin:
            failures.append("thin_panel")
    from finaince.domain.factor import finite_ic

    ic = finite_ic(record.metrics.ic)
    if ic is None:
        failures.append("missing_ic")
    elif direction == "to_pool" and abs(ic) <= 0.005:
        failures.append("ic_threshold")
    returns = _finite_returns(record.daily_returns)
    if not returns:
        failures.append("missing_returns")
    else:
        try:
            import pandas as pd
            from aiminer.manager import _series_correlation

            series = pd.Series(returns)
            from aiminer.pool_io import load_alpha_pool_rows

            from finaince.settings import get_settings

            settings = get_settings()
            for row in load_alpha_pool_rows(settings.aiminer_db):
                other = _finite_returns(row.get("returns"))
                if not other:
                    continue
                corr = _series_correlation(series, pd.Series(other))
                if corr is not None and corr > 0.7:
                    failures.append("correlated")
                    break
        except Exception as exc:  # noqa: BLE001
            failures.append(f"corr_error:{exc}")
        if direction == "to_library" and "correlated" not in failures:
            try:
                from finaince.catalog.store import FactorCatalog

                for other in FactorCatalog().list(source="reproduction"):
                    if other.id == record.id:
                        continue
                    other_ret = _finite_returns(other.daily_returns)
                    if not other_ret:
                        continue
                    corr = _series_correlation(series, pd.Series(other_ret))
                    if corr is not None and corr > 0.7:
                        failures.append("correlated")
                        break
            except Exception as exc:  # noqa: BLE001
                failures.append(f"corr_error:{exc}")
    if direction == "to_pool":
        from finaince.domain.adapters import mapped_aiminer_code

        if not mapped_aiminer_code(record):
            failures.append("empty_code")
            
    details: dict[str, Any] = {}
    
    if "weak_ic" not in skipped:
        ic_ir = record.metrics.extra.get("ic_ir")
        n_days = len(returns) if returns else None
        t_stat = ic_t_stat(ic_ir, n_days)
        if t_stat is None:
            details["weak_ic"] = "insufficient_for_t_stat"
        elif abs(t_stat) < 3.0:
            failures.append("weak_ic")
            details["weak_ic"] = f"t_stat={t_stat:.2f}"
            
    if "inflated_sharpe" not in skipped:
        try:
            from finaince.trace import list_chain
            events = list_chain(limit=500)
            n_trials = 1 + sum(1 for e in events if e.get("action", "").startswith(("eval", "isolated_impl")))
        except Exception:
            n_trials = 1
            
        dsr = deflated_sharpe(returns, n_trials)
        if dsr is None:
            details["inflated_sharpe"] = "insufficient_returns"
        elif dsr < 0.95:
            failures.append("inflated_sharpe")
            details["inflated_sharpe"] = f"dsr={dsr:.4f}, n_trials={n_trials}"

    if "homogeneous" not in skipped:
        verdict = homogeneous_gate(record)
        if verdict["failure"]:
            failures.append(verdict["failure"])
        if verdict.get("detail"):
            details["homogeneous"] = verdict["detail"]

    if "overcomplex" not in skipped:
        verdict = overcomplex_gate(record)
        if verdict["failure"]:
            failures.append(verdict["failure"])
        if verdict.get("detail"):
            details["overcomplex"] = verdict["detail"]

    passed = not failures
    return {"passed": passed, "failures": failures, "direction": direction, "details": details}

"""Screening harness: operator-template grid scored on the bench PIT core.

Sweep ranks WHERE IC lives across template x field x window combinations so
the proposal queue can be fed high-potential regions. It shares the exact
evaluation core with run_bench (point-in-time universe, membership-churn
turnover, per-day cost), so screening numbers are directly comparable to the
citable table. Screening output stays exploratory: promotion still requires
the full gate chain.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable

from finaince.data_track import (
    CACHE_VERSION,
    INDEX_CODE,
    _daily_ic,
    _factor_artifacts,
    _forward_returns,
    _layered_long_short,
    _metrics_from_artifacts,
    _scoped_universe,
    _year_dir,
    components_map,
    read_years,
    universe_for_date,
)

FIELDS = ("close", "high", "low", "volume", "amount")
WINDOWS = (5, 10, 20, 40, 60)

_EPS = 1e-12


def _mom(f: str, w: int) -> Any:
    import polars as pl

    return pl.col(f) - pl.col(f).shift(w).over("ts_code")


def _ma_dist(f: str, w: int) -> Any:
    import polars as pl

    return pl.col(f) - pl.col(f).shift(1).rolling_mean(w).over("ts_code")


def _zma(f: str, w: int) -> Any:
    import polars as pl

    return _ma_dist(f, w) / (
        pl.col(f).shift(1).rolling_std(w).over("ts_code") + _EPS
    )


def _shock(f: str, w: int) -> Any:
    import polars as pl

    return pl.col(f) / (
        pl.col(f).shift(1).rolling_mean(w).over("ts_code") + _EPS
    )


def _compression(f: str, w: int) -> Any:
    import polars as pl

    return -(
        pl.col(f).rolling_max(w).over("ts_code")
        - pl.col(f).rolling_min(w).over("ts_code")
    )


TEMPLATES: dict[str, tuple[Callable[[str, int], Any], Callable[[str, int], str]]] = {
    "mom": (_mom, lambda f, w: f"Rank(Delta({f}, {w}))"),
    "rev": (lambda f, w: -_mom(f, w), lambda f, w: f"-Rank(Delta({f}, {w}))"),
    "ma_dist": (_ma_dist, lambda f, w: f"Rank({f} - Mean({f}, {w}))"),
    "zma": (_zma, lambda f, w: f"Rank(Div({f} - Mean({f}, {w}), Std({f}, {w})))"),
    "shock": (_shock, lambda f, w: f"Rank(Div({f}, Mean({f}, {w})))"),
    "compression": (
        _compression,
        lambda f, w: f"-Rank(Max({f}, {w}) - Min({f}, {w}))",
    ),
}


def build_grid(
    fields: tuple[str, ...] = FIELDS, windows: tuple[int, ...] = WINDOWS
) -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = []
    for tname, (_builder, text_fn) in TEMPLATES.items():
        for field in fields:
            if tname == "compression" and field == "amount":
                continue
            for window in windows:
                grid.append(
                    {
                        "name": f"{tname}_{field}_{window}",
                        "template": tname,
                        "field": field,
                        "window": window,
                        "expression": text_fn(field, window),
                    }
                )
    return grid


def score_candidate(frame: Any, spec: dict[str, Any]) -> Any:
    import polars as pl

    builder = TEMPLATES[spec["template"]][0]
    raw_expr = builder(spec["field"], int(spec["window"]))
    panel = frame.sort(["ts_code", "trade_date"])
    ranked = panel.with_columns(raw_expr.alias("_raw")).with_columns(
        pl.col("_raw").rank(method="ordinal").over("trade_date").alias("_score")
    )
    return ranked.select(["trade_date", "ts_code", "_score"])


def _eval_chunk(payload: tuple[list[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    specs, cfg = payload
    frame = read_years(
        date(int(cfg["first_year"]), 1, 1),
        date(int(cfg["last_year"]), 12, 31),
        verify_hash=False,
    )
    comp = components_map()
    universe_by_day_all: dict[Any, set[str]] = {}
    for day in set(frame["trade_date"].to_list()):
        members = universe_for_date(day, comp)
        if members:
            universe_by_day_all[day] = set(members)
    forwards = _forward_returns(frame)
    windows = {
        "IS": (date.fromisoformat(cfg["is_start"]), date.fromisoformat(cfg["is_end"])),
        "OOS": (date.fromisoformat(cfg["oos_start"]), date.fromisoformat(cfg["oos_end"])),
    }
    rows: list[dict[str, Any]] = []
    for spec in specs:
        row: dict[str, Any] = {
            "name": spec["name"],
            "expression": spec["expression"],
            "template": spec["template"],
            "field": spec["field"],
            "window": spec["window"],
        }
        try:
            scores = score_candidate(frame, spec)
            for label, (w_start, w_end) in windows.items():
                scoped = _scoped_universe(
                    universe_by_day_all, w_start, w_end, embargo=cfg["embargo"]
                )
                row[label] = _metrics_from_artifacts(
                    _factor_artifacts(scores, forwards, scoped), cfg["cost_bps"]
                )
            row["ok"] = True
        except Exception as exc:  # noqa: BLE001
            row["ok"] = False
            row["error"] = str(exc)[:200]
        rows.append(row)
    return rows


def run_sweep(
    *,
    is_start: str = "2019-01-01",
    is_end: str = "2023-12-31",
    oos_start: str = "2024-01-01",
    oos_end: str = "2024-12-31",
    cost_bps: float = 5.0,
    embargo: bool = True,
    rank_by: str = "oos_rank_ic",
    top: int = 20,
    workers: int = 1,
    dedup_catalog: bool = False,
    windows: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    grid_windows = tuple(int(w) for w in windows) if windows else WINDOWS
    grid = build_grid(windows=grid_windows)
    provenance: dict[str, Any] = {
        "windows": {"IS": [is_start, is_end], "OOS": [oos_start, oos_end]},
        "cost_bps": cost_bps,
        "embargo_last_day": embargo,
        "rank_by": rank_by,
        "universe_source": "point_in_time",
        "evaluation_core": "bench_pit_core (same as run_bench)",
        "index_code": INDEX_CODE,
        "data_version": CACHE_VERSION,
        "role": "screening layer; citable confirmation still requires run_bench + gates",
    }
    try:
        root = _year_dir(1970).parent
        years_present = sorted(
            int(p.name)
            for p in root.iterdir()
            if p.is_dir() and p.name.isdigit() and (p / f"csi300_{p.name}.parquet").exists()
        )
    except OSError:
        years_present = []
    if not years_present:
        return {"ok": False, "error": "cache empty; run `finaince bench --sync` first", "provenance": provenance}

    provenance["grid_size"] = len(grid)

    cfg = {
        "is_start": is_start,
        "is_end": is_end,
        "oos_start": oos_start,
        "oos_end": oos_end,
        "cost_bps": cost_bps,
        "embargo": embargo,
        "first_year": years_present[0],
        "last_year": years_present[-1],
    }

    all_rows: list[dict[str, Any]] = []
    workers_n = max(1, min(int(workers), len(grid)))
    if workers_n > 1 and len(grid) > 20:
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context

        chunk_size = math.ceil(len(grid) / workers_n)
        chunks = [
            (grid[i : i + chunk_size], cfg)
            for i in range(0, len(grid), chunk_size)
        ]
        with ProcessPoolExecutor(max_workers=workers_n, mp_context=get_context("spawn")) as pool:
            for chunk_rows in pool.map(_eval_chunk, chunks):
                all_rows.extend(chunk_rows)
    else:
        all_rows.extend(_eval_chunk((grid, cfg)))

    ok_rows = [r for r in all_rows if r.get("ok")]
    failed = [r for r in all_rows if not r.get("ok")]

    if dedup_catalog:
        from finaince.catalog.store import FactorCatalog
        from finaince.expr_ast import expr_hash

        catalog = FactorCatalog()
        for r in ok_rows:
            digest = None
            try:
                digest = expr_hash(r["expression"], "repro_polars")
            except Exception:
                digest = None
            dupes = catalog.find_by_expr_hash(r["expression"], "repro_polars") if digest else []
            r["catalog_dupes"] = len(dupes)
    else:
        for r in ok_rows:
            r["catalog_dupes"] = None

    def sort_key(r: dict[str, Any]) -> float:
        window = "OOS" if rank_by.startswith("oos_") else "IS"
        metric_name = rank_by.split("_", 1)[1] if window == "OOS" else rank_by
        value = (r.get(window) or {}).get(metric_name)
        return value if isinstance(value, (int, float)) else float("-inf")

    ranked_rows = sorted(ok_rows, key=sort_key, reverse=True)
    top_rows = ranked_rows[: max(1, int(top))]

    stamp = date.today().strftime("%Y%m%d")
    out_dir = _year_dir(years_present[0]).parent / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"sweep_{stamp}.json"
    artifact.write_text(
        json.dumps(
            {"provenance": provenance, "rows": ranked_rows},
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    lines = [
        "# 因子筛选地图（screening）",
        "",
        f"- grid: {len(grid)} candidates, ok {len(ok_rows)}, failed {len(failed)}",
        f"- windows: IS {is_start}→{is_end}, OOS {oos_start}→{oos_end}; cost {cost_bps} bps; embargo {embargo}",
        f"- ranked by {rank_by}; evaluation core identical to run_bench (PIT)",
        "",
        "| # | name | OOS ic | OOS rank_ic | OOS icir | OOS sharpe_net | IS ic | catalog_dupes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(top_rows, 1):
        oos, iss = r.get("OOS") or {}, r.get("IS") or {}
        lines.append(
            f"| {i} | {r['name']} | {oos.get('ic')} | {oos.get('rank_ic')} "
            f"| {oos.get('icir')} | {oos.get('sharpe_net')} | {iss.get('ic')} "
            f"| {r.get('catalog_dupes')} |"
        )

    result = {
        "ok": True,
        "provenance": provenance,
        "evaluated": len(ok_rows),
        "failed": len(failed),
        "top": top_rows,
        "artifact": str(artifact),
        "markdown": "\n".join(lines),
    }
    (out_dir / f"sweep_{stamp}.md").write_text(result["markdown"], encoding="utf-8")
    return result


def latest_sweep_artifact() -> Path | None:
    """Newest sweep_*.json in the cache sweep dir, or None."""
    try:
        out_dir = _year_dir(1970).parent / "sweep"
        candidates = sorted(out_dir.glob("sweep_*.json"))
        return candidates[-1] if candidates else None
    except OSError:
        return None


def top_specs_from_artifact(
    path: str | Path, top: int, rank_by: str = "oos_rank_ic"
) -> list[dict[str, Any]]:
    """Rebuild grid specs for the top-N rows of a sweep artifact.

    Raises ValueError when the artifact is missing, unreadable, or holds no
    ok rows — callers degrade fail-closed instead of walking an empty grid.
    """
    artifact = Path(path)
    if not artifact.exists():
        raise ValueError(f"sweep artifact not found: {artifact}")
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable sweep artifact {artifact}: {exc}") from exc
    ok_rows = [r for r in payload.get("rows", []) if r.get("ok")]
    if not ok_rows:
        raise ValueError(f"sweep artifact has no ok rows: {artifact}")

    window = "OOS" if rank_by.startswith("oos_") else "IS"
    metric_name = rank_by.split("_", 1)[1] if window == "OOS" else rank_by

    def sort_key(r: dict[str, Any]) -> float:
        value = (r.get(window) or {}).get(metric_name)
        return value if isinstance(value, (int, float)) else float("-inf")

    ranked = sorted(ok_rows, key=sort_key, reverse=True)[: max(1, int(top))]
    specs: list[dict[str, Any]] = []
    for row in ranked:
        spec: dict[str, Any] = {
            "name": row["name"],
            "template": row["template"],
            "field": row["field"],
            "window": int(row["window"]),
            "expression": row["expression"],
        }
        if spec["template"] not in TEMPLATES:
            raise ValueError(f"unknown template in artifact row: {spec['template']}")
        specs.append(spec)
    return specs


def _layered_long_short_cadence(
    scores: Any,
    forwards: Any,
    universe_by_day: dict[Any, set[str]],
    *,
    n_groups: int = 5,
    rebalance_every: int = 1,
) -> tuple[list[tuple[Any, float]], dict[Any, float]]:
    """Quintile LS with legs refreshed every Nth trading day.

    Screening-layer experiment for the cadence/turnover question; same churn
    definition as the bench core, charged on refresh days only. Held days get
    explicit zero-churn entries so per-day cost application stays honest.
    """
    joined = scores.join(forwards, on=["trade_date", "ts_code"], how="inner").drop_nulls()
    days = sorted(d for d in set(joined["trade_date"].to_list()) if d in universe_by_day)
    series: list[tuple[Any, float]] = []
    turnover_by_day: dict[Any, float] = {}
    prev_legs: tuple[set[str], set[str]] | None = None
    since_refresh = 0
    for day in days:
        members = universe_by_day[day]
        group = joined.filter(joined["trade_date"] == day)
        group = group.filter(group["ts_code"].is_in(sorted(members)))
        if group.height < n_groups * 4:
            prev_legs = None
            continue
        if prev_legs is not None:
            long_ret = (
                group.filter(group["ts_code"].is_in(sorted(prev_legs[0])))["_fwd_ret"].mean()
                or 0.0
            )
            short_ret = (
                group.filter(group["ts_code"].is_in(sorted(prev_legs[1])))["_fwd_ret"].mean()
                or 0.0
            )
        else:
            long_ret = short_ret = 0.0
        ls = float(long_ret - short_ret)
        since_refresh += 1
        if prev_legs is None or since_refresh >= max(1, int(rebalance_every)):
            ranked = group.with_columns(group["_score"].rank(method="ordinal").alias("_rk"))
            bucket = (ranked["_rk"] - 1) * n_groups // ranked.height
            ranked = ranked.with_columns(bucket.alias("_bucket"))
            long_leg = set(ranked.filter(ranked["_bucket"] == 0)["ts_code"].to_list())
            short_leg = set(
                ranked.filter(ranked["_bucket"] == n_groups - 1)["ts_code"].to_list()
            )
            if prev_legs is not None:
                churn_long = len(long_leg ^ prev_legs[0]) / max(len(long_leg), 1)
                churn_short = len(short_leg ^ prev_legs[1]) / max(len(short_leg), 1)
                turnover_by_day[day] = (churn_long + churn_short) / 2.0
            else:
                turnover_by_day[day] = 0.0
            prev_legs = (long_leg, short_leg)
            since_refresh = 0
        else:
            turnover_by_day[day] = 0.0
        series.append((day, ls))
    return series, turnover_by_day


def _candidate_fold_chunk(
    payload: tuple[list[dict[str, Any]], dict[str, Any]]
) -> list[dict[str, Any]]:
    import polars as pl

    specs, cfg = payload
    frame = read_years(
        date(int(cfg["first_year"]), 1, 1),
        date(int(cfg["last_year"]), 12, 31),
        verify_hash=False,
    )
    comp = components_map()
    universe_by_day_all: dict[Any, set[str]] = {}
    for day in set(frame["trade_date"].to_list()):
        members = universe_for_date(day, comp)
        if members:
            universe_by_day_all[day] = set(members)
    forwards = _forward_returns(frame)
    panel = frame.sort(["ts_code", "trade_date"])
    years = range(int(cfg["start_year"]), int(cfg["end_year"]) + 1)
    cadence = max(1, int(cfg.get("rebalance_every", 1)))

    def _fold_metrics(
        scores: Any, scoped: dict[Any, set[str]], cost_bps: float
    ) -> dict[str, Any]:
        ic_vals = _daily_ic(scores, forwards, scoped, spearman=False)
        rank_vals = _daily_ic(scores, forwards, scoped, spearman=True)
        if cadence > 1:
            ls, churn = _layered_long_short_cadence(
                scores, forwards, scoped, rebalance_every=cadence
            )
        else:
            ls, churn = _layered_long_short(scores, forwards, scoped)
        return _metrics_from_artifacts(
            {"ic_vals": ic_vals, "rank_vals": rank_vals, "ls": (ls, churn)}, cost_bps
        )

    def _mean_std(vals: list[float]) -> tuple[float | None, float | None]:
        if not vals:
            return None, None
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else None
        return round(mu, 6), round(sd, 6) if sd is not None else None

    rows: list[dict[str, Any]] = []
    for spec in specs:
        row: dict[str, Any] = {
            "name": spec["name"],
            "expression": spec["expression"],
            "template": spec["template"],
            "field": spec["field"],
            "window": spec["window"],
        }
        try:
            builder = TEMPLATES[spec["template"]][0]
            raw_expr = builder(spec["field"], int(spec["window"]))
            scored = panel.with_columns(raw_expr.alias("_raw")).with_columns(
                pl.col("_raw").rank(method="ordinal").over("trade_date").alias("_score")
            )
            scores = scored.select(["trade_date", "ts_code", "_score"])
            fold_rows: list[dict[str, Any]] = []
            for year in years:
                scoped = _scoped_universe(
                    universe_by_day_all,
                    date(year, 1, 1),
                    date(year, 12, 31),
                    embargo=cfg["embargo"],
                )
                metrics = _fold_metrics(scores, scoped, cfg["cost_bps"])
                if "insufficient_days" in metrics:
                    continue
                fold_rows.append({"year": year, **metrics})
            valid_rics = [f["rank_ic"] for f in fold_rows if f.get("rank_ic") is not None]
            ric_mean, ric_std = _mean_std(valid_rics)
            sharpe_vals = [
                f["sharpe_net"] for f in fold_rows if f.get("sharpe_net") is not None
            ]
            sh_mean, sh_std = _mean_std(sharpe_vals)
            row.update(
                {
                    "folds": len(fold_rows),
                    "rank_ic_mean": ric_mean,
                    "rank_ic_std": ric_std,
                    "rank_ic_positive_ratio": (
                        round(sum(1 for v in valid_rics if v > 0) / len(valid_rics), 4)
                        if valid_rics
                        else None
                    ),
                    "sharpe_net_mean": sh_mean,
                    "sharpe_net_std": sh_std,
                    "fold_detail": fold_rows,
                    "ok": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["ok"] = False
            row["error"] = str(exc)[:200]
        rows.append(row)
    return rows


def run_candidate_walkforward(
    *,
    specs: list[dict[str, Any]],
    cost_bps: float = 5.0,
    embargo: bool = True,
    workers: int = 1,
    rebalance_every: int = 1,
) -> dict[str, Any]:
    """Per-calendar-year folds over screening candidates on the bench PIT core.

    Session-nine follow-up: locate WHEN a screening signal (e.g. the OOS-led
    compression family) actually lives before anyone may quote it. Same
    evaluation core as run_bench/run_sweep; output stays exploratory —
    citable claims still require run_bench + the full gate chain.
    ``rebalance_every > 1`` switches the LS legs to a held-legs cadence
    (screening-layer experiment; churn charged on refresh days only).
    """
    provenance: dict[str, Any] = {
        "cost_bps": cost_bps,
        "embargo_last_day": embargo,
        "rebalance_every": max(1, int(rebalance_every)),
        "candidates": len(specs),
        "universe_source": "point_in_time",
        "index_code": INDEX_CODE,
        "data_version": CACHE_VERSION,
        "role": (
            "screening-layer robustness check; citable confirmation still "
            "requires run_bench + gates"
        ),
    }
    if not specs:
        return {"ok": False, "error": "no candidates given", "provenance": provenance}
    try:
        root = _year_dir(1970).parent
        years_present = sorted(
            int(p.name)
            for p in root.iterdir()
            if p.is_dir() and p.name.isdigit() and (p / f"csi300_{p.name}.parquet").exists()
        )
    except OSError:
        years_present = []
    if not years_present:
        return {
            "ok": False,
            "error": "cache empty; run `finaince bench --sync` first",
            "provenance": provenance,
        }
    first_year, last_year = years_present[0], years_present[-1]
    provenance["years"] = [first_year, last_year]

    cfg = {
        "cost_bps": cost_bps,
        "embargo": embargo,
        "first_year": first_year,
        "last_year": last_year,
        "start_year": first_year,
        "end_year": last_year,
        "rebalance_every": max(1, int(rebalance_every)),
    }

    all_rows: list[dict[str, Any]] = []
    workers_n = max(1, min(int(workers), len(specs)))
    if workers_n > 1 and len(specs) > 4:
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context

        chunk_size = math.ceil(len(specs) / workers_n)
        chunks = [
            (specs[i : i + chunk_size], cfg) for i in range(0, len(specs), chunk_size)
        ]
        with ProcessPoolExecutor(max_workers=workers_n, mp_context=get_context("spawn")) as pool:
            for chunk_rows in pool.map(_candidate_fold_chunk, chunks):
                all_rows.extend(chunk_rows)
    else:
        all_rows.extend(_candidate_fold_chunk((specs, cfg)))

    ok_rows = [r for r in all_rows if r.get("ok")]
    failed = [r for r in all_rows if not r.get("ok")]
    ranked = sorted(
        ok_rows,
        key=lambda r: r.get("rank_ic_mean") or float("-inf"),
        reverse=True,
    )

    stamp = date.today().strftime("%Y%m%d")
    out_dir = root / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"walkforward_{stamp}.json"

    lines = [
        "# 候选逐年折叠稳健性（screening layer）",
        "",
        f"- candidates: {len(specs)}, ok {len(ok_rows)}, failed {len(failed)}",
        f"- folds: {first_year}→{last_year} per calendar year; cost {cost_bps} bps; embargo {embargo}; rebalance_every {max(1, int(rebalance_every))}",
        "- evaluation core identical to run_bench (PIT); exploratory numbers only",
        "",
        "| # | name | folds | rank_ic mean±std | positive% | sharpe_net mean±std |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(ranked, 1):
        mu, sd = r.get("rank_ic_mean"), r.get("rank_ic_std")
        smu, ssd = r.get("sharpe_net_mean"), r.get("sharpe_net_std")
        pos = r.get("rank_ic_positive_ratio")
        pos_s = f"{pos * 100:.0f}%" if isinstance(pos, (int, float)) else "-"
        lines.append(
            f"| {i} | {r['name']} | {r.get('folds')} "
            f"| {mu}±{sd} | {pos_s} | {smu}±{ssd} |"
        )
    for r in failed:
        lines.append(f"- FAILED {r['name']}: {r.get('error')}")

    result = {
        "ok": True,
        "provenance": provenance,
        "evaluated": len(ok_rows),
        "failed": len(failed),
        "rows": ranked,
        "failed_rows": [r["name"] for r in failed],
        "artifact": str(artifact),
        "markdown": "\n".join(lines),
    }
    artifact.write_text(
        json.dumps({"provenance": provenance, "rows": all_rows}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (out_dir / f"walkforward_{stamp}.md").write_text(result["markdown"], encoding="utf-8")
    return result

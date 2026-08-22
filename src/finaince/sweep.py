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
from typing import Any, Callable

from finaince.data_track import (
    CACHE_VERSION,
    INDEX_CODE,
    _factor_artifacts,
    _forward_returns,
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

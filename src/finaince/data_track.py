"""WS-D true-universe numeric track: point-in-time CSI300 cache + citable benchmark.

SPIKE CONCLUSION (build_backtest_bundle): reproagent's bundle constructs its own
DataLoader internally and its local branch is fixture-shaped; it cannot consume
an explicit point-in-time stock pool without upstream changes. Per the plan's
fallback clause this module implements an independent polars-vectorized bench
(IC/RankIC/ICIR + quintile long-short net of cost) straight off the versioned
parquet cache. It never routes through the bundle.

Discipline: every number carries a provenance block (window, cost_bps,
universe_source, data_version); live RQ fetches are import- and credential-
gated and are exercised only under `-m live`; missing cache years fail closed
with the exact list of what is absent.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

CACHE_VERSION = "v1"
INDEX_CODE = "000300.XSHG"
PANEL_FIELDS = ["trade_date", "ts_code", "open", "high", "low", "close", "volume", "amount"]

SEED_FACTORS: list[tuple[str, str]] = [
    ("rank_delta_20", "Rank(Delta(close, 20))"),
    ("reversal_5", "-Rank(Delta(close, 5))"),
    ("vp_shock", "Rank(Div(volume, Mean(volume, 20)))"),
]


def track_root() -> Path:
    from finaince.settings import get_settings

    root = get_settings().home / "data_track" / "csi300" / CACHE_VERSION
    root.mkdir(parents=True, exist_ok=True)
    return root


def manifest_path() -> Path:
    return track_root() / ".manifest.json"


def write_manifest(fields: list[str] | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": CACHE_VERSION,
        "index_code": INDEX_CODE,
        "last_updated": datetime.now(UTC).isoformat(),
        "fields": fields or PANEL_FIELDS,
    }
    manifest_path().write_text(json.dumps(payload, indent=1))
    return payload


def read_manifest() -> dict[str, Any] | None:
    path = manifest_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _year_dir(year: int) -> Path:
    directory = track_root() / str(year)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_year_frame(year: int, frame: Any) -> Path:
    import polars as pl

    target = _year_dir(year) / f"csi300_{year}.parquet"
    frame = frame if isinstance(frame, pl.DataFrame) else pl.from_pandas(frame)
    frame.write_parquet(target)
    (target.parent / (target.name + ".sha256")).write_text(_sha256_of(target))
    return target


def write_components(asof: date, codes: list[str]) -> Path:
    import polars as pl

    directory = track_root() / "constituents"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"components_{asof.isoformat()}.parquet"
    pl.DataFrame({"ts_code": sorted(codes)}).write_parquet(target)
    return target


def read_years(start: date, end: date, *, verify_hash: bool = True) -> Any:
    """Concatenate cached yearly panels covering [start, end]. Fail closed."""
    import polars as pl

    frames: list[Any] = []
    missing: list[int] = []
    for year in range(start.year, end.year + 1):
        target = _year_dir(year) / f"csi300_{year}.parquet"
        if not target.exists():
            missing.append(year)
            continue
        if verify_hash:
            sidecar = target.parent / (target.name + ".sha256")
            expected = sidecar.read_text().strip() if sidecar.exists() else ""
            actual = _sha256_of(target)
            if not expected or expected != actual:
                raise ValueError(f"data_track cache integrity failure for {year}: sha256 mismatch")
        frames.append(pl.read_parquet(target))
    if missing:
        raise FileNotFoundError(f"data_track cache incomplete; missing years: {missing}")
    if not frames:
        raise FileNotFoundError("data_track cache empty")
    return pl.concat(frames, how="vertical")


def rebalance_dates(start: date, end: date) -> list[date]:
    """CSI300 semiannual rebalances: second Friday of June and December."""

    def second_friday(year: int, month: int) -> date:
        first_weekday = calendar.weekday(year, month, 1)
        offset = (4 - first_weekday) % 7
        return date(year, month, 1 + offset + 7)

    out: list[date] = []
    for year in range(start.year, end.year + 1):
        for month in (6, 12):
            candidate = second_friday(year, month)
            if start <= candidate <= end:
                out.append(candidate)
    return out


def components_map() -> dict[date, list[str]]:
    directory = track_root() / "constituents"
    out: dict[date, list[str]] = {}
    if not directory.is_dir():
        return out
    import polars as pl

    for path in sorted(directory.glob("components_*.parquet")):
        token = path.stem.removeprefix("components_")
        try:
            asof = date.fromisoformat(token)
        except ValueError:
            continue
        out[asof] = pl.read_parquet(path)["ts_code"].to_list()
    return out


def universe_for_date(day: date, comp: dict[date, list[str]]) -> list[str]:
    """Point-in-time members: snapshot of the latest rebalance on/before ``day``."""
    eligible = [d for d in comp if d <= day]
    if not eligible:
        return []
    return comp[max(eligible)]


def has_rq_credentials() -> bool:
    return bool((os.environ.get("RQ_USER") or "").strip()) and bool(
        (os.environ.get("RQ_PASS") or "").strip()
    )


def _require_rq():
    if not has_rq_credentials():
        raise RuntimeError("RQ credentials missing (RQ_USER/RQ_PASS); live fetch refused")
    try:
        import rqdatac
    except ImportError as exc:
        raise RuntimeError(f"rqdatac unavailable: {exc}") from exc
    return rqdatac


def fetch_year_live(year: int) -> Path:
    """Live CSI300 OHLCV for one year into the cache (`-m live` only)."""
    rq = _require_rq()
    import pandas as pd
    import polars as pl

    start = date(year, 1, 1)
    end = min(date(year, 12, 31), date.today())
    order_book_ids = rq.index_components(INDEX_CODE, start_date=start, end_date=end)
    frame = rq.get_price(
        list(order_book_ids),
        start_date=start,
        end_date=end,
        frequency="1d",
        fields=["open", "high", "low", "close", "volume", "amount"],
        adjust_type="pre",
        expect_df=True,
    )
    pdf = frame.reset_index()
    rename = {}
    for column in pdf.columns:
        lowered = str(column).lower()
        if "order_book" in lowered or "code" in lowered:
            rename[column] = "ts_code"
        if "date" in lowered or "trading" in lowered:
            rename[column] = "trade_date"
    pdf = pdf.rename(columns=rename)
    if "trade_date" not in pdf.columns or "ts_code" not in pdf.columns:
        raise ValueError(f"unexpected rq frame columns: {list(pdf.columns)}")
    keep = [c for c in PANEL_FIELDS if c in pdf.columns]
    casts = [pl.col("trade_date").cast(pl.Date)] + [
        pl.col(c).cast(pl.Float64) for c in keep if c not in ("trade_date", "ts_code")
    ]
    pl_frame = pl.from_pandas(pd.DataFrame(pdf)[keep]).with_columns(*casts)
    return write_year_frame(year, pl_frame)


def fetch_components_live(asof: date) -> Path:
    rq = _require_rq()
    ids = rq.index_components(INDEX_CODE, start_date=asof, end_date=asof)
    return write_components(asof, [str(x) for x in ids])


def sync_cache(start_year: int, end_year: int) -> dict[str, Any]:
    """Fetch price years + rebalance-date constituent snapshots (live)."""
    done: list[int] = []
    errors: list[str] = []
    for year in range(start_year, end_year + 1):
        try:
            fetch_year_live(year)
            done.append(year)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{year}: {exc}")
    comps: list[str] = []
    for day in rebalance_dates(date(start_year, 1, 1), date(end_year, 12, 31)):
        try:
            fetch_components_live(day)
            comps.append(day.isoformat())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"components {day}: {exc}")
    write_manifest()
    return {"ok": not errors, "years_done": done, "component_dates": comps, "errors": errors}


def _factor_scores(frame: Any, name: str) -> Any:
    import polars as pl

    panel = frame.sort(["ts_code", "trade_date"])
    if name == "rank_delta_20":
        raw = panel.with_columns(
            (pl.col("close") - pl.col("close").shift(20).over("ts_code")).alias("_raw")
        )
    elif name == "reversal_5":
        raw = panel.with_columns(
            -(pl.col("close") - pl.col("close").shift(5).over("ts_code")).alias("_raw")
        )
    elif name == "vp_shock":
        raw = panel.with_columns(
            (
                pl.col("volume")
                / (pl.col("volume").shift(1).rolling_mean(20).over("ts_code") + 1e-12)
            ).alias("_raw")
        )
    else:
        raise ValueError(f"unknown seed factor: {name}")
    ranked = raw.with_columns(
        pl.col("_raw").rank(method="ordinal").over("trade_date").alias("_score")
    )
    return ranked.select(["trade_date", "ts_code", "_score"])


def _forward_returns(frame: Any) -> Any:
    import polars as pl

    panel = frame.sort(["ts_code", "trade_date"])
    return panel.with_columns(
        (pl.col("close").shift(-1) / pl.col("close") - 1.0)
        .over("ts_code")
        .alias("_fwd_ret")
    ).select(["trade_date", "ts_code", "_fwd_ret"])


def _daily_ic(scores: Any, forwards: Any, universe_by_day: dict[Any, set[str]], *, spearman: bool) -> list[float]:
    joined = scores.join(forwards, on=["trade_date", "ts_code"], how="inner").drop_nulls()
    ics: list[float] = []
    for day, group in joined.group_by("trade_date"):
        members = universe_by_day.get(day[0])
        if members is not None:
            group = group.filter(group["ts_code"].is_in(sorted(members)))
        if group.height < 10:
            continue
        x = group["_score"]
        y = group["_fwd_ret"]
        if spearman:
            x = x.rank(method="average")
            y = y.rank(method="average")
        sx = x.std()
        sy = y.std()
        if sx == 0 or sy == 0 or sx is None or sy is None:
            continue
        covariance = ((x - x.mean()) * (y - y.mean())).mean()
        ics.append(float(covariance / (sx * sy)))
    return ics


def _layered_long_short(
    scores: "Any",
    forwards: "Any",
    universe_by_day: dict[Any, set[str]],
    *,
    n_groups: int = 5,
) -> tuple[list[tuple[Any, float]], dict[Any, float]]:
    """Quintile LS daily returns + per-day membership-churn turnover.

    Churn is the mean symmetric-difference fraction of the two legs — a
    participation proxy, not dollar turnover; declared as such in provenance.
    """
    joined = scores.join(forwards, on=["trade_date", "ts_code"], how="inner").drop_nulls()
    days = sorted(set(joined["trade_date"].to_list()))
    series: list[tuple[Any, float]] = []
    prev_members: tuple[set[str], set[str]] | None = None
    turnover_by_day: dict[Any, float] = {}
    for day in days:
        group = joined.filter(joined["trade_date"] == day)
        members = universe_by_day.get(day)
        if members is not None:
            group = group.filter(group["ts_code"].is_in(sorted(members)))
        need = n_groups * 4
        if group.height < need:
            if prev_members is not None:
                prev_members = None
            continue
        ranked = group.with_columns(group["_score"].rank(method="ordinal").alias("_rk"))
        bucket = (ranked["_rk"] - 1) * n_groups // ranked.height
        ranked = ranked.with_columns(bucket.alias("_bucket"))
        long_leg = set(ranked.filter(ranked["_bucket"] == 0)["ts_code"].to_list())
        short_leg = set(ranked.filter(ranked["_bucket"] == n_groups - 1)["ts_code"].to_list())
        long_ret = group.filter(group["ts_code"].is_in(sorted(long_leg)))["_fwd_ret"].mean() or 0.0
        short_ret = group.filter(group["ts_code"].is_in(sorted(short_leg)))["_fwd_ret"].mean() or 0.0
        ls = float(long_ret - short_ret)
        if prev_members is not None:
            churn_long = len(long_leg ^ prev_members[0]) / max(len(long_leg), 1)
            churn_short = len(short_leg ^ prev_members[1]) / max(len(short_leg), 1)
            turnover_by_day[day] = (churn_long + churn_short) / 2.0
        prev_members = (long_leg, short_leg)
        series.append((day, ls))
    return series, turnover_by_day


def _window_metrics(
    ls_series: list[tuple[Any, float]],
    cost_bps: float,
    turnover_by_day: dict[Any, float] | None = None,
) -> dict[str, Any]:
    """Net-of-cost metrics; cost applied per-day when a turnover series is given."""
    from finaince.domain.scoring import max_drawdown, sharpe_ratio

    values = [r for _, r in ls_series]
    n = len(values)
    if n < 20:
        return {"insufficient_days": n}
    net_values: list[float] = []
    for day, raw in ls_series:
        churn = turnover_by_day.get(day, 0.0) if turnover_by_day else 0.0
        net_values.append(raw - (cost_bps / 10000.0) * churn)
    mean = sum(net_values) / n
    sharpe = sharpe_ratio(net_values)
    drawdown = max_drawdown(net_values)
    ar = mean * 252.0
    turnover_mean = (
        sum(turnover_by_day.values()) / len(turnover_by_day)
        if turnover_by_day
        else 0.0
    )
    return {
        "days": n,
        "sharpe_net": round(sharpe, 4) if sharpe is not None else None,
        "ar_net": round(ar, 6),
        "max_drawdown": round(drawdown, 4),
        "turnover": round(turnover_mean, 4),
    }


def run_bench(
    *,
    is_start: str = "2019-01-01",
    is_end: str = "2023-12-31",
    oos_start: str = "2024-01-01",
    oos_end: str = "2024-12-31",
    cost_bps: float = 5.0,
    verify_hash: bool = True,
) -> dict[str, Any]:
    """Citable double-window benchmark table over the cached point-in-time universe."""

    windows = {
        "IS": (date.fromisoformat(is_start), date.fromisoformat(is_end)),
        "OOS": (date.fromisoformat(oos_start), date.fromisoformat(oos_end)),
    }
    provenance = {
        "windows": {name: {"start": w[0].isoformat(), "end": w[1].isoformat()} for name, w in windows.items()},
        "cost_bps": cost_bps,
        "universe_source": "point_in_time",
        "index_code": INDEX_CODE,
        "data_version": CACHE_VERSION,
        "turnover_model": "per_day_quintile_membership_churn (participation proxy, not dollar turnover)",
        "manifest": read_manifest(),
    }
    try:
        earliest_needed = min(w[0] for w in windows.values())
        latest_needed = max(w[1] for w in windows.values())
        missing_probe = [
            year
            for year in range(earliest_needed.year, latest_needed.year + 1)
            if not (_year_dir(year) / f"csi300_{year}.parquet").exists()
        ]
        if missing_probe:
            raise FileNotFoundError(f"data_track cache incomplete; missing years: {missing_probe}")
        comp = components_map()
        if not comp:
            raise FileNotFoundError(
                "constituents cache empty; run `finaince bench --sync` with RQ credentials first"
            )
        earliest = min(comp)
        frame = read_years(max(earliest, earliest_needed), latest_needed, verify_hash=verify_hash)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "provenance": provenance}

    universe_by_day_all: dict[Any, set[str]] = {}
    for day in set(frame["trade_date"].to_list()):
        members = universe_for_date(day, comp)
        if members:
            universe_by_day_all[day] = set(members)

    forwards = _forward_returns(frame)
    table: list[dict[str, Any]] = []
    for name, expression in SEED_FACTORS:
        scores = _factor_scores(frame, name)
        row: dict[str, Any] = {
            "factor": name,
            "expression": expression,
            "dialect": "repro_polars",
        }
        for label, (w_start, w_end) in windows.items():
            scoped_universe = {
                d: m for d, m in universe_by_day_all.items() if w_start <= d <= w_end
            }
            ic_vals = _daily_ic(scores, forwards, scoped_universe, spearman=False)
            rank_vals = _daily_ic(scores, forwards, scoped_universe, spearman=True)
            ls, turnover = _layered_long_short(scores, forwards, scoped_universe)

            def _icir(vals: list[float]) -> tuple[float | None, float | None]:
                if len(vals) < 20:
                    return None, None
                mu = sum(vals) / len(vals)
                sd = math.sqrt(sum((x - mu) ** 2 for x in vals) / len(vals))
                return round(mu, 6), round(mu / sd, 4) if sd else None

            ic_mean, ic_ir = _icir(ic_vals)
            rank_mean, rank_ir = _icir(rank_vals)
            metrics = _window_metrics(ls, cost_bps, turnover)
            row[label] = {
                "ic": ic_mean,
                "rank_ic": rank_mean,
                "icir": ic_ir,
                **metrics,
            }
        table.append(row)
    return {
        "ok": True,
        "provenance": provenance,
        "table": table,
        "markdown": render_markdown(table, provenance),
    }


def render_markdown(table: list[dict[str, Any]], provenance: dict[str, Any]) -> str:
    lines = [
        "# FinAlpha CSI300 双窗基准表",
        "",
        f"- windows: IS {provenance['windows']['IS']['start']}→{provenance['windows']['IS']['end']}, "
        f"OOS {provenance['windows']['OOS']['start']}→{provenance['windows']['OOS']['end']}",
        f"- cost_bps: {provenance['cost_bps']} (double-sided, applied to quintile LS)",
        f"- universe: csi300 point-in-time ({provenance['index_code']}), data_version {provenance['data_version']}",
        "",
        "| factor | metric | IS | OOS |",
        "|---|---|---|---|",
    ]
    for row in table:
        for metric in ("ic", "rank_ic", "icir", "sharpe_net"):
            lines.append(
                f"| {row['factor']} | {metric} | "
                f"{(row.get('IS') or {}).get(metric)} | {(row.get('OOS') or {}).get(metric)} |"
            )
    return "\n".join(lines)


def doctor_section() -> dict[str, Any]:
    manifest = read_manifest()
    years: list[int] = []
    if track_root().exists():
        for entry in track_root().iterdir():
            if entry.is_dir() and entry.name.isdigit():
                if (_year_dir(int(entry.name)) / f"csi300_{entry.name}.parquet").exists():
                    years.append(int(entry.name))
    comp = components_map()
    return {
        "cache_root": str(track_root()),
        "schema_version": CACHE_VERSION,
        "manifest_present": manifest is not None,
        "years_cached": sorted(years),
        "latest_year": max(years) if years else None,
        "component_dates": len(comp),
        "first_component_date": min(comp).isoformat() if comp else None,
        "rq_creds": has_rq_credentials(),
    }

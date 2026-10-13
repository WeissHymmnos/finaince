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


def write_suspensions(asof: date, codes: list[str]) -> Path:
    """Record names that cannot trade on ``asof`` (suspension day)."""
    import polars as pl

    directory = track_root() / "suspensions"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"suspended_{asof.isoformat()}.parquet"
    pl.DataFrame({"ts_code": sorted({str(c) for c in codes})}).write_parquet(target)
    return target


def suspensions_map() -> dict[date, set[str]]:
    directory = track_root() / "suspensions"
    out: dict[date, set[str]] = {}
    if not directory.is_dir():
        return out
    import polars as pl

    for path in sorted(directory.glob("suspended_*.parquet")):
        token = path.stem.removeprefix("suspended_")
        try:
            asof = date.fromisoformat(token)
        except ValueError:
            continue
        out[asof] = set(pl.read_parquet(path)["ts_code"].to_list())
    return out


def tradable_members(
    day: date,
    comp: dict[date, list[str]],
    suspended: dict[date, set[str]] | None = None,
) -> list[str]:
    """Rebalance snapshot minus names suspended on ``day``."""
    members = universe_for_date(day, comp)
    banned = (suspended if suspended is not None else suspensions_map()).get(day) or set()
    if not banned:
        return members
    return [code for code in members if code not in banned]


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
    user = (os.environ.get("RQ_USER") or "").strip()
    password = (os.environ.get("RQ_PASS") or "").strip()
    try:
        rqdatac.init(user, password)
    except Exception as exc:
        raise RuntimeError(f"rq auth failed: {exc}") from exc
    return rqdatac


def _flatten_components(raw: Any) -> list[str]:
    """index_components returns {date: [ids]} over ranges; union across dates."""
    if isinstance(raw, dict):
        ids: set[str] = set()
        for day_ids in raw.values():
            ids.update(str(x) for x in day_ids)
        return sorted(ids)
    return sorted({str(x) for x in raw})


def fetch_year_live(year: int) -> Path:
    """Live CSI300 OHLCV for one year into the cache (`-m live` only)."""
    rq = _require_rq()
    import pandas as pd
    import polars as pl

    start = date(year, 1, 1)
    end = min(date(year, 12, 31), date.today())
    order_book_ids = _flatten_components(
        rq.index_components(INDEX_CODE, start_date=start, end_date=end)
    )
    frame = rq.get_price(
        order_book_ids,
        start_date=start,
        end_date=end,
        frequency="1d",
        fields=["open", "high", "low", "close", "volume", "total_turnover"],
        adjust_type="post",
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
        if "turnover" in lowered:
            rename[column] = "amount"
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
    raw = rq.index_components(INDEX_CODE, start_date=asof, end_date=asof)
    return write_components(asof, _flatten_components(raw))


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
    for day in rebalance_dates(date(start_year - 1, 11, 1), date(end_year, 12, 31)):
        try:
            fetch_components_live(day)
            comps.append(day.isoformat())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"components {day}: {exc}")
    write_manifest()
    return {"ok": not errors, "years_done": done, "component_dates": comps, "errors": errors}


def _factor_scores(frame: Any, name: str, *, _presorted: bool = False) -> Any:
    import polars as pl

    panel = frame if _presorted else frame.sort(["ts_code", "trade_date"])
    if name == "rank_delta_20":
        raw = panel.with_columns(
            (pl.col("close") - pl.col("close").shift(20).over("ts_code")).fill_nan(None).alias("_raw")
        )
    elif name == "reversal_5":
        raw = panel.with_columns(
            (-(pl.col("close") - pl.col("close").shift(5).over("ts_code"))).fill_nan(None).alias("_raw")
        )
    elif name == "vp_shock":
        raw = panel.with_columns(
            (
                pl.col("volume")
                / (pl.col("volume").shift(1).rolling_mean(20).over("ts_code") + 1e-12)
            )
            .fill_nan(None)
            .alias("_raw")
        )
    else:
        raise ValueError(f"unknown seed factor: {name}")
    ranked = raw.with_columns(
        pl.col("_raw").rank(method="ordinal").over("trade_date").alias("_score")
    )
    return ranked.select(["trade_date", "ts_code", "_score"])


LIMIT_MOVE_THRESHOLD = 0.095


def _untradable_next_day_expr(columns: list[str]) -> Any:
    """Next-bar tradability: suspended (no volume) or one-word limit bar (ISSUE-R2-02 / [P0-02]).

    A signal on day t is realised on t+1. If t+1 is suspended, or prints a
    one-word limit move (open == high == low == close with |move| >= ~10%),
    the position cannot be entered/exited at the quoted price, so that
    forward return is withheld rather than credited as paper alpha.
    """
    import polars as pl

    have = set(columns)
    conds: list[Any] = []
    if "volume" in have:
        conds.append(pl.col("volume").shift(-1).fill_null(0.0) <= 0.0)
    if {"open", "high", "low"} <= have:
        n_close = pl.col("close").shift(-1)
        flat_bar = ((pl.col("high").shift(-1) - pl.col("low").shift(-1)).abs() < 1e-8) & (
            (pl.col("open").shift(-1) - n_close).abs() < 1e-8
        )
        big_move = (n_close / pl.col("close") - 1.0).abs() >= LIMIT_MOVE_THRESHOLD
        conds.append(flat_bar & big_move)
    if not conds:
        return None
    out = conds[0]
    for extra in conds[1:]:
        out = out | extra
    return out.fill_null(False)


def _forward_returns(frame: Any, *, _presorted: bool = False) -> Any:
    import polars as pl

    panel = frame if _presorted else frame.sort(["ts_code", "trade_date"])
    untradable = _untradable_next_day_expr(list(panel.columns))
    valid = pl.col("close") > 1e-6
    if untradable is not None:
        valid = valid & ~untradable
    return panel.with_columns(
        pl.when(valid)
        .then(pl.col("close").shift(-1) / pl.col("close") - 1.0)
        .otherwise(None)
        .over("ts_code")
        .fill_nan(None)
        .alias("_fwd_ret")
    ).select(["trade_date", "ts_code", "_fwd_ret"])


def _align_date_col(col_name: str, src_dt: Any, target_dt: Any) -> Any:
    import polars as pl

    col = pl.col(col_name)
    if src_dt == target_dt:
        return col
    if target_dt == pl.Date:
        if src_dt in (pl.String, pl.Utf8):
            return col.str.to_date()
        return col.cast(pl.Date)
    elif target_dt in (pl.String, pl.Utf8):
        return col.cast(pl.String)
    elif target_dt == pl.Datetime or (hasattr(pl, "Datetime") and isinstance(target_dt, pl.Datetime)):
        if src_dt in (pl.String, pl.Utf8):
            return col.str.to_datetime()
        return col.cast(pl.Datetime)
    return col.cast(target_dt)


def _make_universe_frame(joined: Any, universe_by_day: dict[Any, set[str]]) -> Any:
    import polars as pl

    target_dt = joined["trade_date"].dtype
    u_days: list[Any] = []
    u_codes: list[str] = []
    for d, members in universe_by_day.items():
        if members:
            u_days.extend([d] * len(members))
            u_codes.extend(members)
    if not u_days:
        return pl.DataFrame(
            {"trade_date": pl.Series([], dtype=target_dt), "ts_code": pl.Series([], dtype=pl.String)}
        )
    u_df = pl.DataFrame({"trade_date": u_days, "ts_code": u_codes})
    src_dt = u_df["trade_date"].dtype
    if src_dt != target_dt:
        u_df = u_df.with_columns(_align_date_col("trade_date", src_dt, target_dt))
    return u_df


def _universe_days_aligned(joined: Any, universe_by_day: dict[Any, set[str]]) -> set[Any]:
    import polars as pl

    if not universe_by_day:
        return set()
    target_dt = joined["trade_date"].dtype
    sample_key = next(iter(universe_by_day))
    if (target_dt == pl.Date and isinstance(sample_key, date)) or (
        target_dt in (pl.String, pl.Utf8) and isinstance(sample_key, str)
    ):
        return set(universe_by_day.keys())
    k_df = pl.DataFrame({"trade_date": list(universe_by_day.keys())})
    src_dt = k_df["trade_date"].dtype
    if src_dt != target_dt:
        k_df = k_df.with_columns(_align_date_col("trade_date", src_dt, target_dt))
    return set(k_df["trade_date"].to_list())


def _daily_ic(
    scores: Any,
    forwards: Any,
    universe_by_day: dict[Any, set[str]],
    *,
    spearman: bool,
    _prejoined: Any = None,
) -> list[float]:
    import polars as pl

    if _prejoined is not None:
        filtered = _prejoined
    else:
        joined = (
            scores.join(forwards, on=["trade_date", "ts_code"], how="inner")
            .fill_nan(None)
            .drop_nulls()
        )
        if joined.is_empty():
            return []
        u_df = _make_universe_frame(joined, universe_by_day)
        filtered = joined.join(u_df, on=["trade_date", "ts_code"], how="inner")

    if filtered.is_empty():
        return []

    if spearman:
        panel = filtered.with_columns(
            pl.col("_score").rank(method="average").over("trade_date").alias("x"),
            pl.col("_fwd_ret").rank(method="average").over("trade_date").alias("y"),
        )
    else:
        panel = filtered.with_columns(
            pl.col("_score").alias("x"),
            pl.col("_fwd_ret").alias("y"),
        )

    agg = (
        panel.group_by("trade_date")
        .agg(
            pl.len().alias("n"),
            pl.col("x").std().alias("sx"),
            pl.col("y").std().alias("sy"),
            ((pl.col("x") - pl.col("x").mean()) * (pl.col("y") - pl.col("y").mean())).mean().alias("cov"),
        )
        .filter(
            (pl.col("n") >= 10)
            & pl.col("sx").is_not_null()
            & pl.col("sy").is_not_null()
            & (pl.col("sx") > 0)
            & (pl.col("sy") > 0)
        )
        .sort("trade_date")
        .select((pl.col("cov") / (pl.col("sx") * pl.col("sy"))).alias("ic"))
    )
    return [v for v in agg["ic"].to_list() if v is not None and math.isfinite(v)]


def _layered_long_short(
    scores: "Any",
    forwards: "Any",
    universe_by_day: dict[Any, set[str]],
    *,
    n_groups: int = 5,
    _prejoined: Any = None,
    _all_days: list[Any] | None = None,
) -> tuple[list[tuple[Any, float]], dict[Any, float]]:
    """Quintile LS daily returns + per-day membership-churn turnover.

    Churn is the mean symmetric-difference fraction of the two legs — a
    participation proxy, not dollar turnover; declared as such in provenance.
    """
    import polars as pl

    if _prejoined is not None:
        filtered = _prejoined
        if _all_days is not None:
            all_days = _all_days
        else:
            u_days_set = _universe_days_aligned(filtered, universe_by_day)
            all_days = sorted(d for d in filtered["trade_date"].unique().to_list() if d in u_days_set)
    else:
        joined = (
            scores.join(forwards, on=["trade_date", "ts_code"], how="inner")
            .fill_nan(None)
            .drop_nulls()
        )
        if joined.is_empty():
            return [], {}
        u_df = _make_universe_frame(joined, universe_by_day)
        filtered = joined.join(u_df, on=["trade_date", "ts_code"], how="inner")
        u_days_set = _universe_days_aligned(joined, universe_by_day)
        all_days = sorted(d for d in joined["trade_date"].unique().to_list() if d in u_days_set)

    need = n_groups * 4

    if filtered.is_empty():
        return [], {}

    with_counts = filtered.with_columns(
        pl.len().over("trade_date").alias("_day_count")
    ).filter(pl.col("_day_count") >= need)

    if with_counts.is_empty():
        return [], {}

    ranked = with_counts.with_columns(
        pl.col("_score").rank(method="ordinal").over("trade_date").alias("_rk")
    ).with_columns(
        ((pl.col("_rk") - 1) * n_groups // pl.col("_day_count")).alias("_bucket")
    )

    daily_agg = (
        ranked.group_by("trade_date")
        .agg(
            pl.col("_fwd_ret").filter(pl.col("_bucket") == 0).mean().alias("long_ret"),
            pl.col("_fwd_ret").filter(pl.col("_bucket") == n_groups - 1).mean().alias("short_ret"),
            pl.col("ts_code").filter(pl.col("_bucket") == 0).alias("long_codes"),
            pl.col("ts_code").filter(pl.col("_bucket") == n_groups - 1).alias("short_codes"),
        )
        .sort("trade_date")
    )

    day_set_valid = set(daily_agg["trade_date"].to_list())
    daily_dict = {r["trade_date"]: r for r in daily_agg.iter_rows(named=True)}

    series: list[tuple[Any, float]] = []
    turnover_by_day: dict[Any, float] = {}
    prev_members: tuple[set[str], set[str]] | None = None

    for day in all_days:
        if day not in day_set_valid:
            if prev_members is not None:
                prev_members = None
            continue
        row = daily_dict[day]
        l_ret = row["long_ret"] or 0.0
        s_ret = row["short_ret"] or 0.0
        ls = float(l_ret - s_ret)
        long_leg = set(row["long_codes"])
        short_leg = set(row["short_codes"])

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


def _icir_of(vals: list[float]) -> tuple[float | None, float | None]:
    if len(vals) < 20:
        return None, None
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((x - mu) ** 2 for x in vals) / len(vals))
    return round(mu, 6), round(mu / sd, 4) if sd else None


def component_gaps() -> list[str]:
    """Expected Jun/Dec rebalance dates with no constituent snapshot on disk.

    A missing snapshot means universe_for_date bridges with the previous
    one; the gap is surfaced here so bridging stays visible, never silent.
    """
    comp = components_map()
    if not comp:
        return []
    present = set(comp)
    first, last = min(present), max(present)
    return [d.isoformat() for d in rebalance_dates(first, last) if d not in present]


def _scoped_universe(
    universe_by_day_all: dict[Any, set[str]],
    w_start: date,
    w_end: date,
    *,
    embargo: bool,
) -> dict[Any, set[str]]:
    days = sorted(d for d in universe_by_day_all if w_start <= d <= w_end)
    if embargo and len(days) > 1:
        # The last day's forward return uses the next window's close.
        days = days[:-1]
    return {d: universe_by_day_all[d] for d in days}


def _factor_artifacts(
    scores: Any,
    forwards: Any,
    scoped_universe: dict[Any, set[str]],
    *,
    _u_df: Any = None,
    _u_days_set: set[Any] | None = None,
) -> dict[str, Any]:
    joined = (
        scores.join(forwards, on=["trade_date", "ts_code"], how="inner")
        .fill_nan(None)
        .drop_nulls()
    )
    if joined.is_empty():
        return {
            "ic_vals": [],
            "rank_vals": [],
            "ls": ([], {}),
        }
    u_df = _u_df if _u_df is not None else _make_universe_frame(joined, scoped_universe)
    filtered = joined.join(u_df, on=["trade_date", "ts_code"], how="inner")
    if filtered.is_empty():
        return {
            "ic_vals": [],
            "rank_vals": [],
            "ls": ([], {}),
        }
    u_days_set = _u_days_set if _u_days_set is not None else _universe_days_aligned(joined, scoped_universe)
    all_days = sorted(d for d in joined["trade_date"].unique().to_list() if d in u_days_set)
    return {
        "ic_vals": _daily_ic(scores, forwards, scoped_universe, spearman=False, _prejoined=filtered),
        "rank_vals": _daily_ic(scores, forwards, scoped_universe, spearman=True, _prejoined=filtered),
        "ls": _layered_long_short(scores, forwards, scoped_universe, _prejoined=filtered, _all_days=all_days),
    }


def _metrics_from_artifacts(artifacts: dict[str, Any], cost_bps: float) -> dict[str, Any]:
    ic_mean, ic_ir = _icir_of(artifacts["ic_vals"])
    rank_mean, rank_ir = _icir_of(artifacts["rank_vals"])
    ls, turnover = artifacts["ls"]
    return {
        "ic": ic_mean,
        "rank_ic": rank_mean,
        "icir": ic_ir,
        "rank_icir": rank_ir,
        **_window_metrics(ls, cost_bps, turnover),
    }


def _neutralize_scores(frame: Any, name: str, controls: list[str], *, _presorted: bool = False) -> Any:
    """Cross-sectional OLS residuals of seed ``name`` against control seeds."""
    import polars as pl

    joined = _factor_scores(frame, name, _presorted=_presorted)
    for i, ctrl in enumerate(controls):
        joined = joined.join(
            _factor_scores(frame, ctrl, _presorted=_presorted).rename({"_score": f"_c{i}"}),
            on=["trade_date", "ts_code"],
            how="inner",
        )
    joined = joined.drop_nulls(subset=["_score", *[f"_c{i}" for i in range(len(controls))]])

    if joined.is_empty():
        return joined.select(["trade_date", "ts_code", "_score"])

    if not controls:
        y = pl.col("_score")
        resid = y - y.mean().over("trade_date")
        return joined.with_columns(resid.alias("_score")).select(["trade_date", "ts_code", "_score"])

    if len(controls) == 1:
        y = pl.col("_score")
        x = pl.col("_c0")
        dy = y - y.mean().over("trade_date")
        dx = x - x.mean().over("trade_date")
        var_x = (dx * dx).mean().over("trade_date")
        cov_xy = (dx * dy).mean().over("trade_date")
        beta = pl.when(var_x > 1e-12).then(cov_xy / var_x).otherwise(0.0)
        resid = dy - beta * dx
        return joined.with_columns(resid.alias("_score")).select(["trade_date", "ts_code", "_score"])

    control_cols = [f"_c{i}" for i in range(len(controls))]

    def _resid(group: "pl.DataFrame") -> "pl.DataFrame":
        import numpy as np

        y = group["_score"].to_numpy().astype(float)
        X = np.column_stack(
            [np.ones(len(group))]
            + [group[c].to_numpy().astype(float) for c in control_cols]
        )
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        return group.with_columns(pl.Series("_score", y - X @ coef))

    parts = [_resid(g) for g in joined.partition_by("trade_date")]
    return pl.concat(parts).select(["trade_date", "ts_code", "_score"])


def run_bench(
    *,
    is_start: str = "2019-01-01",
    is_end: str = "2023-12-31",
    oos_start: str = "2024-01-01",
    oos_end: str = "2024-12-31",
    cost_bps: float = 5.0,
    verify_hash: bool = True,
    costs: list[float] | None = None,
    embargo: bool = True,
    neutralize_vs: list[str] | None = None,
    expression: str | None = None,
    neutralize_industry: bool = False,
) -> dict[str, Any]:
    """Citable double-window benchmark table over the cached point-in-time universe."""

    windows = {
        "IS": (date.fromisoformat(is_start), date.fromisoformat(is_end)),
        "OOS": (date.fromisoformat(oos_start), date.fromisoformat(oos_end)),
    }
    provenance = {
        "windows": {name: {"start": w[0].isoformat(), "end": w[1].isoformat()} for name, w in windows.items()},
        "cost_bps": cost_bps,
        "costs": sorted(set(costs)) if costs else [cost_bps],
        "embargo_last_day": embargo,
        "neutralize_vs": neutralize_vs or [],
        "universe_source": "point_in_time",
        "index_code": INDEX_CODE,
        "data_version": CACHE_VERSION,
        "turnover_model": "quintile_membership_churn",
        "turnover_model_note": "per-day quintile membership churn; participation proxy, not dollar turnover",
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

    provenance["component_gaps"] = component_gaps()

    factor_names = [name for name, _ in SEED_FACTORS]
    controls: list[str] = []
    if neutralize_vs:
        invalid = [c for c in neutralize_vs if c not in factor_names]
        if invalid:
            return {"ok": False, "error": f"unknown neutralize_vs seeds: {invalid}", "provenance": provenance}
        controls = [c for c in neutralize_vs]

    neutralization = "off"
    if neutralize_industry:
        if "industry" not in frame.columns:
            neutralization = "skipped_no_industry"
        else:
            neutralization = "industry"
    provenance["neutralization"] = neutralization
    provenance["neutralized"] = neutralization == "industry"

    frame = frame.sort(["ts_code", "trade_date"])
    suspended = suspensions_map()
    universe_by_day_all: dict[Any, set[str]] = {}
    for day in frame["trade_date"].unique().to_list():
        members = tradable_members(day, comp, suspended)
        if members:
            universe_by_day_all[day] = set(members)

    forwards = _forward_returns(frame, _presorted=True)
    from finaince.eval.pit_core import TURNOVER_MODEL, score_expression

    # Precompute scoped universe and DataFrame per window once across all factors
    window_cache: dict[str, tuple[dict[Any, set[str]], Any, set[Any], int]] = {}
    for label, (w_start, w_end) in windows.items():
        scoped = _scoped_universe(universe_by_day_all, w_start, w_end, embargo=embargo)
        u_df = _make_universe_frame(forwards, scoped)
        u_days_set = _universe_days_aligned(forwards, scoped)
        names_set: set[str] = set()
        for members in scoped.values():
            names_set.update(members)
        window_cache[label] = (scoped, u_df, u_days_set, len(names_set))

    table: list[dict[str, Any]] = []
    artifacts_by_factor: dict[str, dict[str, dict[str, Any]]] = {}
    factor_rows: list[tuple[str, str]] = list(SEED_FACTORS)
    if expression:
        factor_rows.append(("expression", expression))
    for name, expr in factor_rows:
        if name == "expression":
            try:
                scores = score_expression(frame, expr)
            except ValueError as exc:
                return {"ok": False, "error": str(exc), "provenance": provenance}
        elif neutralize_vs and neutralization != "skipped_no_industry":
            scores = _neutralize_scores(frame, name, [c for c in controls if c != name], _presorted=True)
        else:
            scores = _factor_scores(frame, name, _presorted=True)
        row: dict[str, Any] = {
            "factor": name,
            "expression": expr,
            "dialect": "repro_polars",
            "neutralized": provenance["neutralized"],
        }
        factor_arts: dict[str, dict[str, Any]] = {}
        for label, (w_start, w_end) in windows.items():
            scoped, u_df, u_days_set, n_names = window_cache[label]
            arts = _factor_artifacts(scores, forwards, scoped, _u_df=u_df, _u_days_set=u_days_set)
            factor_arts[label] = arts
            m = _metrics_from_artifacts(arts, cost_bps)
            m["n_names"] = n_names
            m["n_days"] = m.get("days")
            m["cost_bps"] = cost_bps
            m["turnover_model"] = TURNOVER_MODEL
            m["engine"] = "pit_core"
            m["window_start"] = w_start.isoformat()
            m["window_end"] = w_end.isoformat()
            m["embargo_last_day"] = embargo
            m["is_start"] = is_start
            m["is_end"] = is_end
            m["oos_start"] = oos_start
            m["oos_end"] = oos_end
            if "icir" not in m and "ic_ir" in m:
                m["icir"] = m["ic_ir"]
            row[label] = m
        artifacts_by_factor[name] = factor_arts
        table.append(row)

    result: dict[str, Any] = {
        "ok": True,
        "provenance": provenance,
        "table": table,
        "markdown": render_markdown(table, provenance),
    }

    if costs and len(set(costs)) > 1:
        curve_rows: list[dict[str, Any]] = []
        for c in sorted(set(costs)):
            for name, _ in SEED_FACTORS:
                entry: dict[str, Any] = {"cost_bps": c, "factor": name}
                for label in windows:
                    m = _metrics_from_artifacts(artifacts_by_factor[name][label], c)
                    entry[label] = {"sharpe_net": m.get("sharpe_net"), "ar_net": m.get("ar_net")}
                curve_rows.append(entry)
        result["cost_curve"] = curve_rows
        lines = ["", "# 成本敏感度（net Sharpe / 年化）", "", "| cost_bps | factor | IS sharpe | OOS sharpe | IS ar | OOS ar |", "|---|---|---|---|---|---|"]
        for entry in curve_rows:
            lines.append(
                f"| {entry['cost_bps']} | {entry['factor']} "
                f"| {entry['IS']['sharpe_net']} | {entry['OOS']['sharpe_net']} "
                f"| {entry['IS']['ar_net']} | {entry['OOS']['ar_net']} |"
            )
        result["markdown"] += "\n".join(lines)

    return result


def run_walkforward(
    *,
    start: str = "2019-01-01",
    end: str = "2023-12-31",
    cost_bps: float = 5.0,
    embargo: bool = True,
    neutralize_vs: list[str] | None = None,
    verify_hash: bool = True,
) -> dict[str, Any]:
    """Per-calendar-year folds; IC mean/std across folds plus positive-fold ratio."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    provenance: dict[str, Any] = {
        "folds": [str(y) for y in range(s.year, e.year + 1)],
        "cost_bps": cost_bps,
        "embargo_last_day": embargo,
        "neutralize_vs": neutralize_vs or [],
        "universe_source": "point_in_time",
        "index_code": INDEX_CODE,
        "data_version": CACHE_VERSION,
    }
    try:
        missing_probe = [
            year
            for year in range(s.year, e.year + 1)
            if not (_year_dir(year) / f"csi300_{year}.parquet").exists()
        ]
        if missing_probe:
            raise FileNotFoundError(f"data_track cache incomplete; missing years: {missing_probe}")
        comp = components_map()
        if not comp:
            raise FileNotFoundError("constituents cache empty; run `finaince bench --sync` first")
        earliest = min(comp)
        frame = read_years(
            max(earliest, date(s.year, 1, 1)), date(e.year, 12, 31), verify_hash=verify_hash
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "provenance": provenance}

    factor_names = [name for name, _ in SEED_FACTORS]
    controls: list[str] = []
    if neutralize_vs:
        invalid = [c for c in neutralize_vs if c not in factor_names]
        if invalid:
            return {"ok": False, "error": f"unknown neutralize_vs seeds: {invalid}", "provenance": provenance}
        controls = list(neutralize_vs)

    frame = frame.sort(["ts_code", "trade_date"])
    suspended = suspensions_map()
    universe_by_day_all: dict[Any, set[str]] = {}
    for day in frame["trade_date"].unique().to_list():
        members = tradable_members(day, comp, suspended)
        if members:
            universe_by_day_all[day] = set(members)
    forwards = _forward_returns(frame, _presorted=True)

    rows: list[dict[str, Any]] = []
    for name, expression in SEED_FACTORS:
        if neutralize_vs:
            scores = _neutralize_scores(frame, name, [c for c in controls if c != name], _presorted=True)
        else:
            scores = _factor_scores(frame, name, _presorted=True)
        fold_rows: list[dict[str, Any]] = []
        for year in range(s.year, e.year + 1):
            scoped = _scoped_universe(
                universe_by_day_all, date(year, 1, 1), date(year, 12, 31), embargo=embargo
            )
            metrics = _metrics_from_artifacts(_factor_artifacts(scores, forwards, scoped), cost_bps)
            if "insufficient_days" in metrics:
                continue
            fold_rows.append({"year": year, **metrics})
        valid_ics = [f["ic"] for f in fold_rows if f.get("ic") is not None]
        valid_rics = [f["rank_ic"] for f in fold_rows if f.get("rank_ic") is not None]
        sharpe_vals = [f["sharpe_net"] for f in fold_rows if f.get("sharpe_net") is not None]

        def _mean_std(vals: list[float]) -> tuple[float | None, float | None]:
            if not vals:
                return None, None
            mu = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else None
            return round(mu, 6), round(sd, 6) if sd is not None else None

        ic_mean, ic_std = _mean_std(valid_ics)
        ric_mean, ric_std = _mean_std(valid_rics)
        sh_mean, sh_std = _mean_std(sharpe_vals)
        rows.append(
            {
                "factor": name,
                "expression": expression,
                "folds": len(fold_rows),
                "ic_mean": ic_mean,
                "ic_std": ic_std,
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
            }
        )
    lines = [
        "# Walk-forward 折叠稳健性",
        "",
        f"- folds: {provenance['folds']}, cost_bps {cost_bps}, embargo_last_day {embargo}",
        "",
        "| factor | folds | IC mean±std | rank_ic mean±std | rank_ic>0 比例 | sharpe_net mean±std |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['factor']} | {r['folds']} "
            f"| {r['ic_mean']}±{r['ic_std']} | {r['rank_ic_mean']}±{r['rank_ic_std']} "
            f"| {r['rank_ic_positive_ratio']} | {r['sharpe_net_mean']}±{r['sharpe_net_std']} |"
        )
    return {"ok": True, "provenance": provenance, "rows": rows, "markdown": "\n".join(lines)}


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
    years_sorted = sorted(years)
    comp = components_map()
    manifest_file = manifest_path()
    manifest_hash = _sha256_of(manifest_file) if manifest_file.is_file() else None
    return {
        "cache_root": str(track_root()),
        "schema_version": CACHE_VERSION,
        "manifest_present": manifest is not None,
        "manifest_hash": manifest_hash,
        "years_cached": years_sorted,
        "year_coverage": years_sorted,
        "latest_year": max(years_sorted) if years_sorted else None,
        "component_dates": len(comp),
        "constituent_date_coverage": len(comp),
        "first_component_date": min(comp).isoformat() if comp else None,
        "rq_creds": has_rq_credentials(),
    }

"""Runtime compatibility shims for pinned reproagent installs in offline CI.

When ``FINAINCE_NO_PATH_HACK=1`` is active (or sibling ``reproagent/src`` is absent),
the installed ``reproagent`` wheel from git may predate the Phase 1 R2 A-share
microstructure execution constraints (``_enrich_trading_constraints`` and
limit/suspension order filtering in ``StrategyBacktester.run``). This module
patches those constraints onto ``reproagent.reproducer.backtester`` at import time
if they are not already present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl


def _enrich_trading_constraints(data: pl.DataFrame) -> pl.DataFrame:
    """Compute A-share daily price limits and mark executable buy/sell status."""
    import polars as pl

    if data.is_empty():
        return data

    code_col = "ts_code" if "ts_code" in data.columns else "asset"
    date_col = "trade_date" if "trade_date" in data.columns else "date"

    df = data.sort([code_col, date_col])

    if "pre_close" in df.columns:
        prev_close_expr = pl.col("pre_close").fill_null(pl.col("close"))
    else:
        prev_close_expr = pl.col("close").shift(1).over(code_col).fill_null(pl.col("close"))

    df = df.with_columns(prev_close_expr.alias("prev_close"))

    limit_ratio = (
        pl.when(pl.col(code_col).str.to_uppercase().str.contains("ST"))
        .then(0.05)
        .when(
            pl.col(code_col).str.starts_with("688")
            | pl.col(code_col).str.starts_with("689")
            | pl.col(code_col).str.starts_with("300")
            | pl.col(code_col).str.starts_with("301")
        )
        .then(0.20)
        .otherwise(0.10)
    )

    df = df.with_columns([
        (((pl.col("prev_close") * (1.0 + limit_ratio) + 0.0001) * 100).floor() / 100.0).alias(
            "up_limit"
        ),
        (((pl.col("prev_close") * (1.0 - limit_ratio) + 0.0001) * 100).floor() / 100.0).alias(
            "down_limit"
        ),
    ])

    vol_col = pl.col("volume").fill_null(0.0) if "volume" in df.columns else pl.lit(1.0)
    open_col = (
        pl.col("open").fill_null(pl.col("close")) if "open" in df.columns else pl.col("close")
    )

    df = df.with_columns([
        vol_col.shift(-1).over(code_col).alias("next_volume"),
        open_col.shift(-1).over(code_col).alias("next_open"),
        pl.col("up_limit").shift(-1).over(code_col).alias("next_up_limit"),
        pl.col("down_limit").shift(-1).over(code_col).alias("next_down_limit"),
    ])

    df = df.with_columns([
        pl.when(pl.col("next_volume").is_null())
        .then(True)
        .otherwise(
            (pl.col("next_volume") > 0) & (pl.col("next_open") < pl.col("next_up_limit") - 1e-4)
        )
        .alias("can_buy"),
        pl.when(pl.col("next_volume").is_null())
        .then(True)
        .otherwise(
            (pl.col("next_volume") > 0) & (pl.col("next_open") > pl.col("next_down_limit") + 1e-4)
        )
        .alias("can_sell"),
    ])

    return df


def _patched_backtester_run(
    self,
    factor_values: pl.DataFrame,
    params,
    factor_def,
    data: pl.DataFrame | None = None,
):
    """Group backtest + IC with A-share microstructure execution constraints."""
    import uuid
    from datetime import datetime

    import polars as pl
    from reproagent.models.backtest import BacktestResult
    from reproagent.reproducer.metrics import (
        _as_float,
        compute_group_returns,
        compute_ic,
        compute_max_drawdown,
        compute_sharpe,
    )

    if data is None:
        from reproagent.reproducer.data_loader import DataLoader

        loader = DataLoader(self.settings)
        data = loader.load_price_data(factor_def.universe, params.start_date, params.end_date)

    if "trade_date" not in data.columns and "date" in data.columns:
        data = data.rename({"date": "trade_date"})
    if "ts_code" not in data.columns and "asset" in data.columns:
        data = data.rename({"asset": "ts_code"})

    delay = max(1, int(getattr(params, "delay", 1) or 1))
    shift_expr = (
        pl.col("close").shift(-1).over("ts_code")
        if delay == 1
        else pl.col("close").shift(-delay).over("ts_code")
    )
    data = data.sort(["ts_code", "trade_date"]).with_columns(
        pl.when(pl.col("close").abs() > 1e-12)
        .then(shift_expr / pl.col("close") - 1)
        .otherwise(None)
        .alias("forward_return")
    )

    data = _enrich_trading_constraints(data)
    raw_fwd = data["forward_return"] if "forward_return" in data.columns else None
    if bool(getattr(params, "limit_no_fill", True)):
        if (
            "forward_return" in data.columns
            and "can_buy" in data.columns
            and "can_sell" in data.columns
            and raw_fwd is not None
        ):
            data = data.with_columns(
                pl.when(~pl.col("can_buy"))
                .then(pl.lit(None))
                .when((~pl.col("can_sell")) | (raw_fwd < 0))
                .then(raw_fwd)
                .otherwise(pl.col("forward_return"))
                .alias("forward_return")
            )

    forward_returns = data.select(
        [pl.col("trade_date").alias("date"), pl.col("ts_code").alias("asset"), "forward_return"]
    ).drop_nulls()

    ic_df = compute_ic(factor_values, forward_returns)
    if len(ic_df) > 0:
        ic_mean = _as_float(ic_df["ic"].mean())
        ic_std = _as_float(ic_df["ic"].std()) if len(ic_df) > 1 else 0.0
        ic_ir = ic_mean / ic_std if ic_std != 0 else 0.0
    else:
        ic_mean = float("nan")
        ic_ir = float("nan")

    num_groups = params.num_groups
    n_assets = 0
    if "asset" in factor_values.columns and factor_values.height:
        n_assets = int(factor_values.select(pl.col("asset").n_unique()).item() or 0)
    effective_groups = int(num_groups) if n_assets >= int(num_groups or 0) else max(2, n_assets or 2)

    grouped = (
        factor_values.drop_nulls("factor_value")
        .with_columns(pl.col("factor_value").rank(method="ordinal").over("date").alias("rank"))
        .with_columns(
            (pl.col("rank") / (pl.col("rank").max().over("date") + 1) * effective_groups)
            .cast(pl.Int32)
            .alias("group")
        )
    )

    raw_group_returns = compute_group_returns(grouped, forward_returns, effective_groups)
    group_returns = {g: 0.0 for g in range(int(effective_groups))}
    group_returns.update(raw_group_returns)

    df = grouped.join(forward_returns, on=["date", "asset"], how="inner")
    daily_group_ret = df.group_by(["date", "group"]).agg(
        pl.col("forward_return").mean().alias("daily_return")
    )

    long_ret = daily_group_ret.filter(pl.col("group") == effective_groups - 1).rename(
        {"daily_return": "long_ret"}
    )
    short_ret = daily_group_ret.filter(pl.col("group") == 0).rename(
        {"daily_return": "short_ret"}
    )

    ls_ret = (
        long_ret.join(short_ret, on="date", how="inner")
        .with_columns((pl.col("long_ret") - pl.col("short_ret")).alias("ls_return_raw"))
        .sort("date")
    )

    long_weights = grouped.filter(pl.col("group") == effective_groups - 1).with_columns(
        (pl.lit(1.0) / pl.len().over("date")).alias("weight")
    )
    short_weights = grouped.filter(pl.col("group") == 0).with_columns(
        (pl.lit(-1.0) / pl.len().over("date")).alias("weight")
    )
    weights = pl.concat(
        [
            long_weights.select(["date", "asset", "weight"]),
            short_weights.select(["date", "asset", "weight"]),
        ]
    )

    if weights.is_empty() or ls_ret.is_empty():
        date_dtype = weights.schema.get("date", pl.Date) if not weights.is_empty() else pl.Date
        daily_turnover = pl.DataFrame(
            {"date": [], "turnover": []},
            schema={"date": date_dtype, "turnover": pl.Float64},
        )
    else:
        dates_df = (
            weights.select("date")
            .unique()
            .sort("date")
            .with_columns(pl.col("date").shift(1).alias("prev_date"))
        )
        w_t = weights
        w_t_prev = weights.join(dates_df, left_on="date", right_on="prev_date").select(
            [
                pl.col("date_right").alias("date"),
                "asset",
                pl.col("weight").alias("prev_weight"),
            ]
        )
        merged_w = w_t.join(w_t_prev, on=["date", "asset"], how="full", coalesce=True).fill_null(
            0.0
        )
        daily_turnover = (
            merged_w.group_by("date")
            .agg((pl.col("weight") - pl.col("prev_weight")).abs().sum().alias("turnover"))
            .with_columns((pl.col("turnover") / 2.0).alias("turnover"))
        )

    avg_turnover = _as_float(daily_turnover["turnover"].mean())

    cost_rate = params.transaction_cost_bps / 10000.0
    ls_ret = (
        ls_ret.join(daily_turnover, on="date", how="left")
        .fill_null(0.0)
        .with_columns(
            (pl.col("ls_return_raw") - pl.col("turnover") * cost_rate).alias("ls_return")
        )
    )

    ls_series = ls_ret["ls_return"]

    sharpe = compute_sharpe(ls_series)
    equity_curve = (1 + ls_series).cum_prod()
    mdd = compute_max_drawdown(equity_curve)
    ann_return = _as_float(ls_series.mean()) * 252 if len(ls_series) > 0 else 0.0

    output_dir = self.settings.data_dir / "backtest" / factor_def.id
    output_dir.mkdir(parents=True, exist_ok=True)

    factor_values_path = output_dir / "factor_values.parquet"
    factor_values.write_parquet(factor_values_path)

    equity_curve_path = output_dir / "equity_curve.parquet"
    ls_ret.write_parquet(equity_curve_path)

    ic_path = output_dir / "ic.parquet"
    ic_df.write_parquet(ic_path)

    return BacktestResult(
        id=str(uuid.uuid4()),
        config_id="default",
        factor_id=factor_def.id,
        engine="polars",
        start_date=params.start_date,
        end_date=params.end_date,
        group_annualized_returns=group_returns,
        ic_mean=ic_mean,
        ic_ir=float(ic_ir),
        long_short_annual_return=ann_return,
        sharpe_ratio=sharpe,
        max_drawdown=mdd,
        turnover=avg_turnover,
        factor_values_path=factor_values_path,
        equity_curve_path=equity_curve_path,
        computed_at=datetime.now(),
    )


def ensure_reproagent_compat() -> None:
    """Attach A-share microstructure constraints to older reproagent installs."""
    try:
        from reproagent.models import replication as _rep
        from reproagent.reproducer import backtester as _bt
    except ImportError:
        return

    if not hasattr(_bt, "_enrich_trading_constraints"):
        _bt._enrich_trading_constraints = _enrich_trading_constraints  # type: ignore[attr-defined]
        _bt.StrategyBacktester.run = _patched_backtester_run  # type: ignore[method-assign]

    try:
        from pydantic import ConfigDict

        if "limit_no_fill" not in getattr(_rep.BacktestParams, "model_fields", {}):
            _rep.BacktestParams.model_config = ConfigDict(extra="allow")
            _rep.BacktestParams.model_rebuild(force=True)
    except Exception as _exc:
        from finaince._swallow import log_swallowed

        log_swallowed("finaince._reproagent_compat:ensure_reproagent_compat", _exc)

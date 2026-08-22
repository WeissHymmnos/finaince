from datetime import date

import polars as pl
import pytest

from finaince.runtime import packaged_local_panel, panel_path, panel_stats
from finaince.settings import doctor_report


def test_panel_path_default():
    assert panel_path() == packaged_local_panel()

def test_panel_path_env_valid(monkeypatch, tmp_path):
    fixture = packaged_local_panel()
    if fixture is None:
        pytest.skip("No packaged local panel")
    
    # Copy fixture to tmp_path
    import shutil
    dest = tmp_path / "prices.parquet"
    shutil.copy(fixture / "prices.parquet", dest)
    
    monkeypatch.setenv("FINAINCE_PANEL_PATH", str(dest))
    assert panel_path() == dest

def test_panel_path_env_invalid(monkeypatch):
    monkeypatch.setenv("FINAINCE_PANEL_PATH", "/does/not/exist/prices.parquet")
    with pytest.raises(ValueError, match="FINAINCE_PANEL_PATH does not exist or is not readable"):
        panel_path()

def test_panel_stats_fixture():
    fixture = packaged_local_panel()
    if fixture is None:
        pytest.skip("No packaged local panel")
    
    stats = panel_stats(fixture)
    assert stats["n_assets"] == 2
    assert stats["thin"] is True

def test_panel_stats_wide(tmp_path):
    # Synthesize a wide panel
    dates = [date(2024, 1, 1).replace(day=d % 28 + 1, month=d // 28 + 1) for d in range(70)]
    assets = [f"{i:06d}.XSHE" for i in range(60)]
    
    # Create combinations
    import itertools
    rows = list(itertools.product(dates, assets))
    
    df = pl.DataFrame({
        "trade_date": [r[0] for r in rows],
        "ts_code": [r[1] for r in rows],
        "close": [10.0] * len(rows),
        "volume": [100] * len(rows)
    })
    
    dest = tmp_path / "prices.parquet"
    df.write_parquet(dest)
    
    stats = panel_stats(dest)
    assert stats["n_assets"] == 60
    assert stats["n_days"] == 70
    assert stats["thin"] is False

def test_doctor_report_keys():
    report = doctor_report()
    assert "panel_path" in report
    assert "panel_stats" in report
    assert "ricequant_creds" in report
    assert "universe_claim_warning" in report
    
    assert isinstance(report["ricequant_creds"], bool)
    assert isinstance(report["panel_stats"], dict)
    assert "n_assets" in report["panel_stats"]
    assert "n_days" in report["panel_stats"]
    
    if report["panel_stats"].get("thin"):
        assert report["universe_claim_warning"] == "thin"
    else:
        assert report["universe_claim_warning"] is None


# --- WS-D: bench pipeline on a synthetic hermetic cache ----------------------


def _seed_cache(home, years=(2023, 2024), n_assets=40):
    import random
    from datetime import date as d

    import polars as pl

    from finaince.data_track import (
        rebalance_dates,
        track_root,
        write_components,
        write_manifest,
        write_year_frame,
    )

    rng = random.Random(11)
    codes = [f"6{i:05d}.XSHG" for i in range(n_assets)]
    prices = {c: 10.0 for c in codes}
    for year in years:
        rows = []
        day = d(year, 1, 3)
        end = d(year, 12, 28)
        while day <= end:
            if day.weekday() < 5:
                for c in codes:
                    prices[c] *= 1.0 + rng.gauss(0.0005, 0.02)
                    rows.append(
                        {
                            "trade_date": day,
                            "ts_code": c,
                            "open": prices[c] * 0.999,
                            "high": prices[c] * 1.01,
                            "low": prices[c] * 0.99,
                            "close": round(prices[c], 4),
                            "volume": float(rng.randint(1_000_000, 5_000_000)),
                            "amount": float(rng.randint(1_000_000, 5_000_000)) * prices[c],
                        }
                    )
            day = d.fromordinal(day.toordinal() + 1)
        write_year_frame(year, pl.DataFrame(rows))
    for rb in rebalance_dates(d(years[0], 1, 1), d(years[-1], 12, 31)):
        write_components(rb, codes[:30] if rb.month == 6 else codes[10:])
    write_manifest()
    return track_root()


def test_bench_hermetic_pipeline(isolated_home):
    from finaince.data_track import run_bench

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    result = run_bench(
        is_start="2019-01-01",
        is_end="2023-12-31",
        oos_start="2024-01-01",
        oos_end="2024-12-31",
        cost_bps=5.0,
    )
    assert result["ok"] is True, result.get("error")
    prov = result["provenance"]
    assert prov["universe_source"] == "point_in_time"
    assert prov["cost_bps"] == 5.0
    assert prov["data_version"] == "v1"
    assert len(result["table"]) == 3
    for row in result["table"]:
        for window_label in ("IS", "OOS"):
            block = row[window_label]
            assert block["ic"] is not None or block.get("insufficient_days")
            assert "sharpe_net" in block or "insufficient_days" in block
            assert block["turnover"] >= 0.0
    assert "| factor | metric | IS | OOS |" in result["markdown"]


def test_bench_missing_cache_fails_closed_with_years(isolated_home):
    from finaince.data_track import run_bench

    result = run_bench()
    assert result["ok"] is False
    assert "missing years" in result["error"]
    assert "2019" in result["error"] and "2024" in result["error"]


def test_bench_sha256_integrity_check(isolated_home):
    import pytest

    from finaince.data_track import read_years, run_bench

    root = _seed_cache(isolated_home)
    target = root / "2023" / "csi300_2023.parquet"
    target.write_bytes(target.read_bytes() + b"\x00")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        read_years(__import__("datetime").date(2023, 1, 1), __import__("datetime").date(2023, 12, 31))
    result = run_bench(is_start="2023-01-01", is_end="2023-12-31", oos_start="2024-01-01", oos_end="2024-12-31")
    assert result["ok"] is False and "sha256 mismatch" in result["error"]


def test_point_in_time_universe_switches_on_rebalance(isolated_home):
    from datetime import date as d

    from finaince.data_track import rebalance_dates, universe_for_date, write_components

    rbs = rebalance_dates(d(2024, 1, 1), d(2024, 12, 31))
    june = [x for x in rbs if x.month == 6][0]
    dec = [x for x in rbs if x.month == 12][0]
    write_components(june, ["A", "B", "C"])
    write_components(dec, ["B", "C", "D"])
    before = set(universe_for_date(june, {june: ["A", "B", "C"], dec: ["B", "C", "D"]}))
    after = set(universe_for_date(dec, {june: ["A", "B", "C"], dec: ["B", "C", "D"]}))
    mid = universe_for_date(d(dec.year, dec.month + 1, 1) if dec.month < 12 else d(dec.year, 12, 31), {june: ["A", "B", "C"], dec: ["B", "C", "D"]})
    assert before == {"A", "B", "C"}
    assert after == {"B", "C", "D"}
    assert mid == ["B", "C", "D"]


def test_rebalance_dates_second_friday():
    from datetime import date as d

    from finaince.data_track import rebalance_dates

    rbs = rebalance_dates(d(2024, 1, 1), d(2024, 12, 31))
    assert rbs == [d(2024, 6, 14), d(2024, 12, 13)]
    for candidate in rbs:
        assert candidate.weekday() == 4
        assert candidate.day <= 14


def test_doctor_reports_data_track_section(isolated_home):
    from finaince.settings import doctor_report

    _seed_cache(isolated_home)
    report = doctor_report()
    section = report["data_track"]
    assert section["manifest_present"] is True
    assert section["years_cached"] == [2023, 2024]
    assert section["latest_year"] == 2024
    assert section["component_dates"] == 4
    assert isinstance(section["rq_creds"], bool)


def test_live_fetch_refused_without_credentials(monkeypatch, isolated_home):
    import pytest as _pytest

    from finaince.data_track import fetch_year_live

    monkeypatch.delenv("RQ_USER", raising=False)
    monkeypatch.delenv("RQ_PASS", raising=False)
    with _pytest.raises(RuntimeError, match="RQ credentials missing"):
        fetch_year_live(2024)


def test_bench_cost_curve_and_embargo_provenance(isolated_home):
    from finaince.data_track import run_bench

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    result = run_bench(
        is_start="2019-01-01",
        is_end="2023-12-31",
        oos_start="2024-01-01",
        oos_end="2024-12-31",
        cost_bps=5.0,
        costs=[0.0, 5.0, 10.0],
    )
    assert result["ok"] is True
    curve = result.get("cost_curve") or []
    assert {e["cost_bps"] for e in curve} == {0.0, 5.0, 10.0}
    assert len(curve) == 9
    assert result["provenance"]["embargo_last_day"] is True


def test_scoped_universe_embargo_drops_last_day():
    from datetime import date as d

    from finaince.data_track import _scoped_universe

    days = [d(2024, 1, i) for i in range(2, 11)]
    universe = {day: {"a", "b"} for day in days}
    plain = _scoped_universe(universe, days[0], days[-1], embargo=False)
    guarded = _scoped_universe(universe, days[0], days[-1], embargo=True)
    assert max(plain) == days[-1]
    assert max(guarded) == days[-2]


def test_walkforward_hermetic(isolated_home):
    from finaince.data_track import run_walkforward

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    result = run_walkforward(start="2019-01-01", end="2023-12-31")
    assert result["ok"] is True, result.get("error")
    assert len(result["rows"]) == 3
    for row in result["rows"]:
        assert row["folds"] >= 4
        assert row["rank_ic_positive_ratio"] is not None

    neutralized = run_walkforward(
        start="2019-01-01",
        end="2023-12-31",
        neutralize_vs=["reversal_5"],
    )
    assert neutralized["ok"] is True

    bad = run_walkforward(neutralize_vs=["not_a_seed"])
    assert bad["ok"] is False


def test_sweep_grid_and_hermetic_run(isolated_home):
    from finaince import sweep as sweep_mod

    grid = sweep_mod.build_grid(windows=(5,))
    names = [c["name"] for c in grid]
    assert len(names) == len(set(names))
    assert any(c["template"] == "compression" for c in grid)
    assert all(c["field"] != "amount" or c["template"] != "compression" for c in grid)

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    result = sweep_mod.run_sweep(windows=(5,), top=5)
    assert result["ok"] is True, result.get("error")
    assert result["evaluated"] == len(grid)
    assert len(result["top"]) == min(5, result["evaluated"])
    from pathlib import Path

    assert Path(result["artifact"]).exists()


def test_top_specs_from_artifact_fail_closed_and_ranked(isolated_home, tmp_path):
    import json

    from finaince import sweep as sweep_mod

    with pytest.raises(ValueError):
        sweep_mod.top_specs_from_artifact(tmp_path / "missing.json", 5)

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"rows": [{"ok": False}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        sweep_mod.top_specs_from_artifact(empty, 5)

    artifact = tmp_path / "sweep_test.json"
    rows = [
        {
            "ok": True,
            "name": "a",
            "template": "mom",
            "field": "close",
            "window": 5,
            "expression": "Rank(Delta(close, 5))",
            "OOS": {"rank_ic": 0.01},
        },
        {
            "ok": True,
            "name": "b",
            "template": "compression",
            "field": "close",
            "window": 10,
            "expression": "-Rank(Max(close, 10) - Min(close, 10))",
            "OOS": {"rank_ic": 0.06},
        },
        {"ok": False, "name": "c"},
    ]
    artifact.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    specs = sweep_mod.top_specs_from_artifact(artifact, 1)
    assert [s["name"] for s in specs] == ["b"]
    assert specs[0]["window"] == 10
    both = sweep_mod.top_specs_from_artifact(artifact, 2)
    assert [s["name"] for s in both] == ["b", "a"]

    quota_rows = list(rows) + [
        {
            "ok": True,
            "name": "d",
            "template": "compression",
            "field": "low",
            "window": 5,
            "expression": "-Rank(Max(low, 5) - Min(low, 5))",
            "OOS": {"rank_ic": 0.05},
        },
        {
            "ok": True,
            "name": "e",
            "template": "rev",
            "field": "close",
            "window": 5,
            "expression": "-Rank(Delta(close, 5))",
            "OOS": {"rank_ic": 0.04},
        },
    ]
    quota_artifact = tmp_path / "sweep_quota.json"
    quota_artifact.write_text(json.dumps({"rows": quota_rows}), encoding="utf-8")
    diversified = sweep_mod.top_specs_from_artifact(
        quota_artifact, 4, per_family=1
    )
    families = [s["template"] for s in diversified]
    assert len(families) == len(set(families))
    assert set(families) <= {"compression", "mom", "rev"}
    assert len(diversified) == 3

    with pytest.raises(ValueError):
        sweep_mod.top_specs_from_artifact(
            _write_artifact_with_template(tmp_path, "nope"), 1
        )


def _write_artifact_with_template(tmp_path, template: str):
    import json

    artifact = tmp_path / "sweep_bad.json"
    artifact.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "ok": True,
                        "name": "x",
                        "template": template,
                        "field": "close",
                        "window": 5,
                        "expression": "Rank(Delta(close, 5))",
                        "OOS": {"rank_ic": 0.05},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return artifact


def test_candidate_walkforward_hermetic(isolated_home):
    from finaince import sweep as sweep_mod

    assert sweep_mod.run_candidate_walkforward(specs=[])["ok"] is False
    assert sweep_mod.run_candidate_walkforward(
        specs=[{"name": "x", "template": "mom", "field": "close", "window": 5, "expression": "Rank(Delta(close, 5))"}]
    )["ok"] is False

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    grid = {c["name"]: c for c in sweep_mod.build_grid(windows=(5,))}
    specs = [grid["mom_close_5"], grid["compression_close_5"]]
    result = sweep_mod.run_candidate_walkforward(specs=specs)
    assert result["ok"] is True, result.get("error")
    assert result["evaluated"] == 2
    assert result["provenance"]["years"] == [2019, 2024]
    for row in result["rows"]:
        assert row["folds"] >= 4
        assert row["rank_ic_positive_ratio"] is not None
        assert row["fold_detail"], row
        for fold in row["fold_detail"]:
            assert fold["year"] in range(2019, 2025)

    from pathlib import Path

    artifact_path = Path(result["artifact"])
    assert artifact_path.exists()
    assert artifact_path.with_suffix(".md").exists()

    bad_spec = dict(grid["mom_close_5"], template="not_a_template")
    broken = sweep_mod.run_candidate_walkforward(specs=[bad_spec])
    assert broken["evaluated"] == 0
    assert broken["failed"] == 1
    assert broken["failed_rows"] == ["mom_close_5"]


def test_layered_long_short_cadence_charges_refresh_days_only():
    from datetime import date as d

    import polars as pl

    from finaince import sweep as sweep_mod

    codes = [f"6{i:05d}.XSHG" for i in range(30)]
    days = [d(2024, 1, 1 + i) if i < 31 else d(2024, 2, 1 + i - 31) for i in range(40)]
    score_rows = []
    fwd_rows = []
    for di, day in enumerate(days):
        for ci, code in enumerate(codes):
            score_rows.append(
                {"trade_date": day, "ts_code": code, "_score": float((ci + di) % 30)}
            )
            fwd_rows.append(
                {"trade_date": day, "ts_code": code, "_fwd_ret": ((ci + di) % 30) / 300.0}
            )
    scores = pl.DataFrame(score_rows)
    forwards = pl.DataFrame(fwd_rows)
    universe = {day: set(codes) for day in days}

    ls_daily, churn_daily = sweep_mod._layered_long_short(scores, forwards, universe)
    ls_weekly, churn_weekly = sweep_mod._layered_long_short_cadence(
        scores, forwards, universe, rebalance_every=5
    )
    assert len(ls_daily) == len(ls_weekly) == len(days)
    assert len(churn_daily) == len(days) - 1  # bench core omits the first day
    assert len(churn_weekly) == len(days)  # cadence variant declares held days explicitly
    refresh_days = [day for day in days if churn_weekly[day] > 0 or day == days[0]]
    assert len(refresh_days) == 8
    assert all(churn_weekly[day] == 0.0 for day in days if day not in refresh_days)
    assert sum(churn_weekly.values()) < sum(churn_daily.values())


def test_candidate_walkforward_rebalance_cadence_hermetic(isolated_home):
    from finaince import sweep as sweep_mod

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    grid = {c["name"]: c for c in sweep_mod.build_grid(windows=(5,))}
    specs = [grid["mom_close_5"], grid["compression_close_5"]]
    result = sweep_mod.run_candidate_walkforward(specs=specs, rebalance_every=5)
    assert result["ok"] is True, result.get("error")
    assert result["provenance"]["rebalance_every"] == 5
    for row in result["rows"]:
        assert row["folds"] >= 4
        weekly_turnover = [
            f["turnover"] for f in row["fold_detail"] if f.get("turnover") is not None
        ]
        assert weekly_turnover
    daily = sweep_mod.run_candidate_walkforward(specs=[grid["mom_close_5"]])
    daily_row = next(r for r in daily["rows"] if r["name"] == "mom_close_5")
    weekly_row = next(r for r in result["rows"] if r["name"] == "mom_close_5")
    daily_t = [f["turnover"] for f in daily_row["fold_detail"]]
    weekly_t = [f["turnover"] for f in weekly_row["fold_detail"]]
    assert sum(weekly_t) <= sum(daily_t)


def test_candidate_walkforward_combine_top(isolated_home):
    from finaince import sweep as sweep_mod

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    grid = {c["name"]: c for c in sweep_mod.build_grid(windows=(5,))}
    specs = [grid["mom_close_5"], grid["compression_close_5"]]

    single = sweep_mod.run_candidate_walkforward(specs=specs, combine_top=1)
    assert single["provenance"]["combine_top"] == 1
    combined_one = next(r for r in single["rows"] if r["name"] == "combined_top_1")
    assert combined_one["ok"] is True, combined_one.get("error")
    assert combined_one["members"] == ["mom_close_5"]
    member = next(r for r in single["rows"] if r["name"] == "mom_close_5")
    for f_c, f_m in zip(combined_one["fold_detail"], member["fold_detail"]):
        assert f_c["year"] == f_m["year"]
        assert f_c["rank_ic"] == pytest.approx(f_m["rank_ic"], abs=1e-9)

    both = sweep_mod.run_candidate_walkforward(specs=specs, combine_top=2)
    combined_two = next(r for r in both["rows"] if r["name"] == "combined_top_2")
    assert combined_two["ok"] is True, combined_two.get("error")
    assert combined_two["folds"] >= 4
    assert combined_two["rank_ic_positive_ratio"] is not None

    none_opt = sweep_mod.run_candidate_walkforward(specs=specs)
    assert all(not r["name"].startswith("combined") for r in none_opt["rows"])
    assert none_opt["provenance"]["combine_top"] is None

    bad_mode = sweep_mod.run_candidate_walkforward(
        specs=specs, combine_weighting="momentum"
    )
    assert bad_mode["ok"] is False
    assert "combine_weighting" in bad_mode["error"]


def test_candidate_walkforward_adaptive_weighting(isolated_home):
    from finaince import sweep as sweep_mod

    _seed_cache(isolated_home, years=tuple(range(2019, 2025)))
    grid = {c["name"]: c for c in sweep_mod.build_grid(windows=(5,))}
    specs = [grid["mom_close_5"], grid["compression_close_5"]]

    single = sweep_mod.run_candidate_walkforward(
        specs=specs[:1],
        combine_top=1,
        combine_weighting="adaptive_inv_vol",
    )
    adaptive_one = next(
        r for r in single["rows"] if r["name"] == "combined_top_1_adaptive"
    )
    member = next(r for r in single["rows"] if r["name"] == "mom_close_5")
    assert adaptive_one["ok"] is True
    for f_c, f_m in zip(adaptive_one["fold_detail"], member["fold_detail"]):
        assert f_c["rank_ic"] == pytest.approx(f_m["rank_ic"], abs=1e-9)

    both = sweep_mod.run_candidate_walkforward(
        specs=specs, combine_top=2, combine_weighting="adaptive_inv_vol"
    )
    combo = next(
        r for r in both["rows"] if r["name"] == "combined_top_2_adaptive"
    )
    assert combo["ok"] is True, combo.get("error")
    wby = combo["weights_by_year"]
    assert set(wby) == {str(y) for y in range(2019, 2025)}
    first_two_neutral = all(
        abs(w - 0.5) < 1e-9 for y in ("2019", "2020") for w in wby[y].values()
    )
    assert first_two_neutral
    for year, weights in wby.items():
        assert pytest.approx(sum(abs(w) for w in weights.values()), abs=1e-6) == 1.0
    later = wby["2023"]
    assert any(abs(w - 0.5) > 1e-6 for w in later.values())

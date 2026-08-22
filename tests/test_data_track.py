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

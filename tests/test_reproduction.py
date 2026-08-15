"""Drive shipped reproagent reproduce_report on the minimal fixture."""

from __future__ import annotations

from pathlib import Path

from finaince.reproduction import reproduce_report, validate_expression


def test_reproduce_report_on_minimal_fixture(sample_report_path: Path, offline_settings) -> None:
    import finreportparser.output  # noqa: F401
    import inspect

    from reproagent.pipeline import reproduce_report as shipped

    source = inspect.getsource(reproduce_report)
    assert "reproagent.pipeline" in source
    assert shipped is not None
    outcome = reproduce_report(sample_report_path, offline_settings)
    assert outcome is not None
    assert "status" in outcome
    assert isinstance(outcome["status"], str)
    assert outcome["status"]
    assert "factors" in outcome
    assert isinstance(outcome["factors"], list)
    has_domain = False
    if outcome.get("factor_id") or outcome.get("factor_ids"):
        has_domain = True
    for factor in outcome["factors"]:
        if any(
            key in factor
            for key in ("metrics", "factor_id", "backtest_result_id", "deviation", "status")
        ):
            has_domain = True
            if factor.get("metrics"):
                assert isinstance(factor["metrics"], dict)
    if not outcome["factors"]:
        # no_factors / invalid still expose a structured status
        has_domain = "status" in outcome
    assert has_domain


def test_validate_expression_shipped_payload() -> None:
    from reproagent.reproducer.polars_engine import validate_expression as shipped

    good = "Rank(Delta(close, 1))"
    bad = "NotARealOp(close)"
    good_out = validate_expression(good)
    bad_out = validate_expression(bad)
    assert good_out == shipped(good)
    assert bad_out == shipped(bad)
    assert good_out["valid"] is True
    assert isinstance(good_out.get("errors"), list)
    assert bad_out["valid"] is False
    assert bad_out["errors"]

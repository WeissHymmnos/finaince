from datetime import UTC, datetime

from finaince.catalog.store import FactorCatalog
from finaince.coaching import diverse_expression_samples, failure_lessons
from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
from finaince.sdk_ext import CUSTOM_TOOLS, allowed_mcp_tool_names
from finaince.tools import handle_research_context
from finaince.trace import append_event


def test_diverse_expression_samples(isolated_home):
    catalog = FactorCatalog()
    
    # Empty catalog
    assert diverse_expression_samples() == []
    
    # Seed 3 rows
    # 1 and 2 are identical, 3 is orthogonal
    dates = [f"2020-01-{i:02d}" for i in range(1, 15)]
    
    # r1: ic=0.5
    r1 = FactorRecord(
        id="r1",
        name="r1",
        expression=FactorExpression(dialect="qlib", text="r1"),
        lineage=FactorLineage(source="manual", source_ref="r1"),
        metrics=FactorMetrics(ic=0.5),
        daily_returns={d: float(i) for i, d in enumerate(dates)},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # r2: ic=0.4, identical returns to r1
    r2 = FactorRecord(
        id="r2",
        name="r2",
        expression=FactorExpression(dialect="qlib", text="r2"),
        lineage=FactorLineage(source="manual", source_ref="r2"),
        metrics=FactorMetrics(ic=0.4),
        daily_returns={d: float(i) for i, d in enumerate(dates)},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # r3: ic=0.3, orthogonal returns
    r3 = FactorRecord(
        id="r3",
        name="r3",
        expression=FactorExpression(dialect="qlib", text="r3"),
        lineage=FactorLineage(source="manual", source_ref="r3"),
        metrics=FactorMetrics(ic=0.3),
        daily_returns={d: float(i % 2) for i, d in enumerate(dates)},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    
    catalog.upsert(r1)
    catalog.upsert(r2)
    catalog.upsert(r3)
    
    samples = diverse_expression_samples(limit=2)
    assert len(samples) == 2
    assert samples[0]["id"] == "r1"
    assert samples[1]["id"] == "r3"
    
    samples_all = diverse_expression_samples(limit=3)
    assert len(samples_all) == 3
    assert samples_all[0]["id"] == "r1"
    assert samples_all[1]["id"] == "r3"
    assert samples_all[2]["id"] == "r2"

def test_failure_lessons(isolated_home):
    append_event(action="test", error="ValueError: bad value", summary="test summary", hypothesis="test hyp")
    append_event(action="test", error="TypeError: bad type", summary="test summary 2", hypothesis="test hyp 2")
    
    lessons = failure_lessons(error_prefix="ValueError")
    assert len(lessons) == 1
    assert lessons[0]["error_head"] == "ValueError"
    assert lessons[0]["summary_short"] == "test summary"
    assert lessons[0]["hypothesis"] == "test hyp"

def test_handle_research_context(isolated_home):
    res = handle_research_context()
    assert res["ok"] is True
    assert "samples" in res
    assert "lessons" in res
    assert res["counts"] == {"samples": 0, "lessons": 0, "chains": 0}
    
    # Test clamp
    res_clamp = handle_research_context(sample_limit=999, lesson_limit=0)
    assert res_clamp["ok"] is True

def test_handle_research_context_error(monkeypatch, isolated_home):
    def mock_diverse(*args, **kwargs):
        raise RuntimeError("mock error")
    
    import finaince.coaching
    monkeypatch.setattr(finaince.coaching, "diverse_expression_samples", mock_diverse)
    
    res = handle_research_context()
    assert res["ok"] is False
    assert res["error"] == "mock error"
    assert res["error_type"] == "RuntimeError"

def test_sdk_tool_registration():
    tool_names = [t.name for t in CUSTOM_TOOLS]
    assert "research_context" in tool_names
    allowed = allowed_mcp_tool_names()
    assert "mcp__finaince__research_context" in allowed

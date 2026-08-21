from finaince.sdk_ext import CUSTOM_TOOLS, allowed_mcp_tool_names
from finaince.tools import handle_recent_failures
from finaince.trace import append_event


def test_handle_recent_failures(isolated_home):
    # Seed trace failures
    append_event(action="isolated_impl", error="ImportError: no module X")
    append_event(action="isolated_impl", error="ValueError: bad value")
    append_event(action="isolated_impl", error="ImportError: no module Y")

    # Test matching
    res = handle_recent_failures("ImportError")
    assert res["ok"] is True
    assert res["count"] == 2
    assert len(res["items"]) == 2
    assert res["items"][0]["error"] == "ImportError: no module Y"
    assert res["items"][1]["error"] == "ImportError: no module X"

    # Test limit clamping
    res_limit_0 = handle_recent_failures("ImportError", limit=0)
    assert res_limit_0["ok"] is True
    assert res_limit_0["count"] == 1
    assert len(res_limit_0["items"]) == 1

    # Seed more to test upper limit
    for i in range(60):
        append_event(action="isolated_impl", error=f"TypeError: {i}")

    res_limit_999 = handle_recent_failures("TypeError", limit=999)
    assert res_limit_999["ok"] is True
    assert res_limit_999["count"] == 50
    assert len(res_limit_999["items"]) == 50

def test_handle_recent_failures_unexpected_error(monkeypatch, isolated_home):
    def mock_recent_failures(*args, **kwargs):
        raise RuntimeError("mock error")

    import finaince.tools
    monkeypatch.setattr(finaince.tools, "recent_failures", mock_recent_failures, raising=False)

    # Also monkeypatch the one inside the function if it imports locally
    # The function does: from finaince.trace import recent_failures
    # So we need to patch finaince.trace.recent_failures
    import finaince.trace
    monkeypatch.setattr(finaince.trace, "recent_failures", mock_recent_failures)

    res = handle_recent_failures("ImportError")
    assert res["ok"] is False
    assert res["error"] == "mock error"
    assert res["error_type"] == "RuntimeError"

def test_sdk_tool_registration():
    tool_names = [t.name for t in CUSTOM_TOOLS]
    assert "recent_failures" in tool_names

    allowed = allowed_mcp_tool_names()
    assert "mcp__finaince__recent_failures" in allowed

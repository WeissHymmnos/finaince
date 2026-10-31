"""Offline regressions for SDK, sandbox and external adjudication boundaries."""

from __future__ import annotations

import json
import sys
import types

import httpx
import pytest

from finaince import agent, brain_track, code_evolution, expr_ast, isolate, sdk_ext


def _brain_client(monkeypatch, handler):
    client = httpx.Client(base_url=brain_track.DEFAULT_BRAIN_BASE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(brain_track, "_client", lambda: client)
    monkeypatch.setattr(brain_track, "has_brain_credentials", lambda: True)
    monkeypatch.delenv("BRAIN_API_BASE", raising=False)
    return client


def test_brain_uses_alpha_id_from_completed_simulation(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/simulations":
            return httpx.Response(201, headers={"Location": "/simulations/simulation-123"})
        if request.url.path == "/simulations/simulation-123":
            return httpx.Response(200, json={"status": "COMPLETE", "alpha": "alpha-456"})
        assert request.url.path == "/alphas/alpha-456"
        return httpx.Response(200, json={"grade": "PASS", "is": {"sharpe": 1.4}})

    client = _brain_client(monkeypatch, handler)
    result = brain_track.submit_expression("rank(close)")
    assert result["ok"] and result["alpha_id"] == "alpha-456"
    assert result["grade"] == "PASS"
    assert paths == ["/simulations", "/simulations/simulation-123", "/alphas/alpha-456"]
    assert client.is_closed


@pytest.mark.parametrize("location", [
    "https://attacker.invalid/simulations/leak",
    "//attacker.invalid/simulations/leak",
    "http://api.worldquantbrain.com/simulations/leak",
    "https://api.worldquantbrain.com:8443/simulations/leak",
    "/simulations/../../secret",
])
def test_brain_rejects_untrusted_location_before_get(monkeypatch, location):
    def handler(request):
        assert request.method == "POST", "untrusted Location must never be fetched"
        return httpx.Response(201, headers={"Location": location})

    _brain_client(monkeypatch, handler)
    result = brain_track.submit_expression("rank(close)")
    assert result["ok"] is False
    assert result["reason"] == "unsafe_simulation_location"


@pytest.mark.parametrize("payload,error", [
    ({"status": "COMPLETE"}, "simulation_missing_alpha_id"),
    ({"status": "ERROR"}, "simulation_failed:ERROR"),
    ({"alpha": "../secret"}, "simulation_invalid_alpha_id"),
])
def test_brain_incomplete_or_failed_simulation_is_not_a_ruling(monkeypatch, payload, error):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/simulations/sim"})
        return httpx.Response(200, json=payload)

    _brain_client(monkeypatch, handler)
    result = brain_track.submit_expression("rank(close)")
    assert result["ok"] is False
    assert result["reason"] == error


def test_brain_alpha_fetch_failure_does_not_report_platform_success(monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, headers={"Location": "/simulations/sim"})
        if request.url.path.startswith("/simulations/"):
            return httpx.Response(200, json={"alpha": "actual-alpha"})
        return httpx.Response(404, json={"error": "not found"})

    _brain_client(monkeypatch, handler)
    result = brain_track.submit_expression("rank(close)")
    assert result["ok"] is False
    assert result["reason"] == "alpha_fetch_failed:404"


def _fake_sdk(monkeypatch, query):
    class ResultMessage:
        def __init__(self, *, is_error=False, result="done"):
            self.is_error = is_error
            self.result = result
            self.subtype = "error_max_turns" if is_error else "success"

    module = types.ModuleType("claude_agent_sdk")
    module.ResultMessage = ResultMessage
    module.TextBlock = type("TextBlock", (), {})
    module.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    monkeypatch.setattr(agent, "build_claude_agent_options", lambda **kwargs: kwargs)
    return ResultMessage


def test_agent_error_result_is_failure(monkeypatch):
    async def query(**kwargs):
        yield result_type(is_error=True, result="turn budget exhausted")

    result_type = _fake_sdk(monkeypatch, query)
    result = agent.run_research_desk("review candidates")
    assert result["ok"] is False
    assert result["error_type"] == "error_max_turns"
    assert "turn budget exhausted" in result["error"]


def test_agent_stream_failure_does_not_repeat_side_effects(monkeypatch):
    calls = []

    async def query(**kwargs):
        calls.append(kwargs)
        yield types.SimpleNamespace(content="created a research job")
        raise ConnectionError("stream disconnected")

    _fake_sdk(monkeypatch, query)
    result = agent.run_research_desk("create a research job", max_turns=3)
    assert result["ok"] is False
    assert len(calls) == 1
    assert calls[0]["options"] == {"max_turns": 3}
    assert "created a research job" in result["text"]


@pytest.mark.parametrize("tool_name", ["Read", "Write", "Edit", "WebFetch", "mcp__other__review_approve"])
def test_sdk_hook_denies_unregistered_tools(tool_name):
    result = sdk_ext.inspect_pre_tool_use({"tool_name": tool_name, "tool_input": {}})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_sdk_hook_limits_delegation_to_registered_specialists():
    for specialist, expected in [("discover", "allow"), ("general-purpose", "deny")]:
        result = sdk_ext.inspect_pre_tool_use({"tool_name": "Agent", "tool_input": {"subagent_type": specialist}})
        assert result["hookSpecificOutput"]["permissionDecision"] == expected


def test_sdk_hook_rejects_missing_configured_pdf_root(monkeypatch, tmp_path):
    monkeypatch.setenv("FINAINCE_PDF_ROOT", str(tmp_path / "missing"))
    result = sdk_ext.inspect_pre_tool_use({
        "tool_name": "mcp__finaince__reproduce_report", "tool_input": {"pdf_path": "/outside/report.pdf"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("expression", [
    "'{0.__class__}'.format(panel)",
    "'{item.__class__}'.format_map({'item': panel})",
    "np.memmap('must-not-exist', mode='w+', shape=(3,))",
    "np.savetxt('must-not-exist', [1, 2, 3])",
    "np.array([1, 2, 3]).tofile('must-not-exist')",
    "pd.read_pickle('must-not-exist')",
    "pl.scan_csv('must-not-exist')",
    "pl.sql('SELECT * FROM read_csv(\"must-not-exist\")')",
])
def test_sandbox_rejects_indirect_io_and_format_reflection(expression):
    with pytest.raises(isolate.ASTSecurityError):
        isolate.validate_factor_ast(f"def compute(panel):\n    {expression}\n    return [1, 2, 3]\n")


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_sandbox_rejects_nonfinite_output(bad):
    result = isolate.child_isolate({"source": f"def compute(panel): return [1, 2, float({json.dumps(bad)})]"})
    assert result == {"ok": False, "error": "compute_must_return_finite_values"}


def test_sandbox_does_not_replace_explicit_empty_panel_with_simulated_data():
    result = isolate.child_isolate({"source": "def compute(panel): return panel['close']", "panel": {}})
    assert result["ok"] is False
    assert "KeyError" in result["error"]


def test_isolate_child_imports_current_source_checkout(monkeypatch, isolated_home, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("FINAINCE_SANDBOX", "off")
    result = isolate.run_isolated("def compute(panel): return [1, 2, 3]")
    assert result["ok"] is True, result
    assert result["values"] == [1.0, 2.0, 3.0]


@pytest.mark.parametrize("left,right", [
    ("Sub(1, close)", "Sub(close, 1)"),
    ("Div(close, 0.01)", "Div(close, 0.02)"),
    ("close - 0", "close - 1"),
    ("If(close, 1, volume)", "If(close, volume, 1)"),
])
def test_expression_identity_preserves_operand_positions_and_scalar_values(left, right):
    assert expr_ast.expr_hash(left) != expr_ast.expr_hash(right)
    assert expr_ast.serialize(expr_ast.parse(left, "repro_polars")) != expr_ast.serialize(
        expr_ast.parse(right, "repro_polars")
    )


@pytest.mark.parametrize("expression", ["close | volume", "$$close", "module.Rank(close)", "1e999"])
def test_expression_ast_does_not_sanitize_unsupported_syntax(expression):
    with pytest.raises(ValueError):
        expr_ast.parse(expression, "qlib")


def test_expression_normalization_does_not_drop_parameterized_rank():
    tree = expr_ast.normalize(expr_ast.parse("Rank(Rank(close, 5), 10)", "repro_polars"))
    assert tree.children[0].op == "rank"
    assert tree.children[0].params == (5.0,)


def test_code_seed_escapes_name_and_expression_as_literals():
    name = 'research"\nNAME = "injected'
    expression = 'Rank(close)"\nEXPRESSION = "other'
    source = code_evolution.default_seed(name, expression)
    result = isolate.child_isolate({"source": source})
    assert result["ok"] is True
    assert result["name"] == name
    assert result["expression"] == expression


def test_ast_edit_motive_counts_removed_lines_and_pure_edits():
    result = code_evolution.ast_edit_motive("a\nb\nc\n", "a\n")
    assert result["removed_lines"] == 2
    assert result["added_lines"] == 0
    assert result["changed_regions"] == 1
    result = code_evolution.ast_edit_motive("a\n", "a\nb\nc\n")
    assert result["added_lines"] == 2
    assert result["changed_regions"] == 1

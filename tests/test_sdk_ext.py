"""Drive shipped Claude Agent SDK custom tools, hook, and session builder."""

from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from finaince.sdk_ext import (
    CUSTOM_TOOLS,
    MCP_SERVER_NAME,
    build_claude_agent_options,
    create_finaince_mcp_server,
    handle_cull_factor_pool,
    handle_score_factor,
    handle_search_library,
    handle_validate_expression,
    inspect_pre_tool_use,
    pre_tool_use_hook,
)


def test_cull_tool_handler_returns_domain_decisions() -> None:
    shared = {f"2024-01-{day:02d}": float(day) / 100.0 for day in range(1, 13)}
    factors = [
        {
            "role": "a",
            "hypothesis": "keep-strong",
            "perf_metric": 0.05,
            "selection_score": 0.05,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "b",
            "hypothesis": "cull-corr",
            "perf_metric": 0.04,
            "selection_score": 0.04,
            "market_profile": "cn_stock",
            "returns": shared,
        },
    ]
    payload = handle_cull_factor_pool(factors)
    assert payload["input_count"] == 2
    assert payload["kept_count"] >= 1
    assert payload["kept"]
    assert any(item.get("id", "").startswith("alpha_") for item in payload["kept"])
    decisions = {d["hypothesis"]: d["decision"] for d in payload["decisions"]}
    assert decisions["keep-strong"] == "keep"
    assert decisions["cull-corr"] == "cull"


def test_score_tool_handler_returns_metrics() -> None:
    payload = handle_score_factor(
        {
            "annualized_return": 0.15,
            "sharpe": 1.1,
            "max_drawdown": 0.1,
            "turnover": 0.3,
            "cost_drag": 0.02,
        },
        factor_ic=0.04,
    )
    assert isinstance(payload["score"], float)
    assert payload["score"] != 0.0
    assert payload["metrics"]["sharpe"] == 1.1


def test_validate_tool_handler_returns_real_payload() -> None:
    ok = handle_validate_expression("Rank(Delta(close, 1))")
    bad = handle_validate_expression("Nope(close)")
    assert ok["valid"] is True
    assert ok["expression"] == "Rank(Delta(close, 1))"
    assert isinstance(ok["errors"], list)
    assert bad["valid"] is False
    assert bad["errors"]


def test_reproduce_tool_handler_returns_factor_status(
    sample_report_path: Path, offline_settings
) -> None:
    from finaince.sdk_ext import handle_reproduce_report

    payload = handle_reproduce_report(str(sample_report_path), settings=offline_settings)
    assert payload.get("status")
    assert "factors" in payload
    assert isinstance(payload["factors"], list)


def test_validate_sdk_tool_async_handler() -> None:
    from finaince.sdk_ext import validate_expression_tool

    result = asyncio.run(validate_expression_tool.handler({"expression": "Rank(Delta(close, 1))"}))
    assert result["content"]
    text = result["content"][0]["text"]
    assert "valid" in text


def test_search_library_handler_returns_search_payload(tmp_path: Path) -> None:
    from reproagent.settings import Settings

    settings = Settings(
        _env_file=None,
        app_env="dev",
        data_dir=tmp_path / "lib",
    )
    payload = handle_search_library(query="momentum", settings=settings)
    assert payload["query"] == "momentum"
    assert payload["count"] == len(payload["items"])
    assert isinstance(payload["items"], list)


def test_desk_handlers_call_shipped_catalog_and_eval(monkeypatch) -> None:
    from finaince.tools import handle_catalog_list, handle_doctor, handle_eval_expression
    from reproagent.settings import get_settings

    doc = handle_doctor()
    assert doc["product_name"] == "FinAlpha"
    listed = handle_catalog_list()
    assert "items" in listed
    fixture = Path(__file__).resolve().parents[2] / "reproagent" / "tests" / "fixtures" / "test_data"
    monkeypatch.setenv("LOCAL_DATA_PATH", str(fixture))
    get_settings.cache_clear()
    ev = handle_eval_expression("Rank(Delta(close, 1))")
    assert ev["ok"] is True
    assert isinstance(ev["metrics"].get("ic_mean"), (int, float))


def test_pre_tool_use_hook_allow_and_deny() -> None:
    allow_event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__finaince__validate_expression",
        "tool_input": {"expression": "Rank(close)"},
        "session_id": "s",
        "transcript_path": "t",
        "cwd": ".",
        "tool_use_id": "1",
    }
    deny_event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
        "session_id": "s",
        "transcript_path": "t",
        "cwd": ".",
        "tool_use_id": "2",
    }
    missing_pdf = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__finaince__reproduce_report",
        "tool_input": {"pdf_path": ""},
        "session_id": "s",
        "transcript_path": "t",
        "cwd": ".",
        "tool_use_id": "3",
    }
    allowed = inspect_pre_tool_use(allow_event)
    denied = inspect_pre_tool_use(deny_event)
    blocked = inspect_pre_tool_use(missing_pdf)
    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert allowed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"

    async_out = asyncio.run(pre_tool_use_hook(deny_event, "2", {}))
    assert async_out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_session_builder_registers_mcp_tools_and_hook() -> None:
    options = build_claude_agent_options()
    assert isinstance(options, ClaudeAgentOptions)
    assert isinstance(options.mcp_servers, dict)
    assert MCP_SERVER_NAME in options.mcp_servers
    server = options.mcp_servers[MCP_SERVER_NAME]
    assert isinstance(server, dict)
    assert server.get("type") == "sdk"
    assert server.get("name") == MCP_SERVER_NAME
    assert server.get("instance") is not None

    rebuilt = create_finaince_mcp_server()
    assert rebuilt["type"] == "sdk"
    assert rebuilt["instance"] is not None

    names = {t.name for t in CUSTOM_TOOLS}
    assert {
        "cull_factor_pool",
        "reproduce_report",
        "validate_expression",
        "search_library",
        "catalog_list",
        "eval_expression",
        "promote_factor",
        "review_approve",
        "review_reject",
        "doctor",
    } <= names
    for tool_name in names:
        assert f"mcp__{MCP_SERVER_NAME}__{tool_name}" in options.allowed_tools

    assert options.hooks is not None
    assert "PreToolUse" in options.hooks
    assert "PostToolUse" in options.hooks
    matchers = options.hooks["PreToolUse"]
    assert matchers
    assert isinstance(matchers[0], HookMatcher)
    assert matchers[0].matcher
    assert pre_tool_use_hook in matchers[0].hooks
    assert options.system_prompt
    assert "FinAlpha" in str(options.system_prompt)
    assert "aiminer" in str(options.system_prompt).lower() or "发现" in str(options.system_prompt)
    assert options.agents
    assert {"discover", "reproduce", "review"} <= set(options.agents)

    from finaince.sdk_ext import describe_session_options, inspect_post_tool_use

    desc = describe_session_options(options)
    assert desc["has_system_prompt"] is True
    assert "discover" in desc["specialists"]
    post = inspect_post_tool_use({"tool_name": "mcp__finaince__catalog_list", "tool_input": {}})
    assert post["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "catalog" in post["hookSpecificOutput"]["additionalContext"]

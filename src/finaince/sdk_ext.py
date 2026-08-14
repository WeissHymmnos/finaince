"""Claude Agent SDK custom system extensions for finaince.

Domain operations are registered as in-process MCP tools via ``@tool`` +
``create_sdk_mcp_server``. A PreToolUse hook is attached on
``ClaudeAgentOptions``. Tool bodies call the merged discovery/reproduction
functions; they do not reimplement them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    create_sdk_mcp_server,
    tool,
)

from finaince._paths import ensure_import_paths

ensure_import_paths()

MCP_SERVER_NAME = "finaince"
MCP_SERVER_VERSION = "0.1.0"

# Tools the PreToolUse hook will deny outright (name or suffix).
_DENIED_TOOL_NAMES = {
    "Bash",
    "bash",
    "mcp__finaince__unsafe",
}


def _json_content(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str, ensure_ascii=False)}]}


def handle_cull_factor_pool(factors: list[dict[str, Any]]) -> dict[str, Any]:
    """IC/correlation cull via the shipped aiminer selector."""
    from finaince.tools import handle_cull_factor_pool as _shared

    return _shared(factors)


def handle_score_factor(
    metrics: dict[str, Any],
    factor_ic: float = 0.0,
    walk_forward: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Composite factor/strategy score via the shipped aiminer selector."""
    from finaince.discovery import score_factor

    score = score_factor(metrics, factor_ic=factor_ic, walk_forward=walk_forward)
    return {"score": score, "metrics": metrics, "factor_ic": factor_ic}


def handle_reproduce_report(
    pdf_path: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    """研报复现 via the shipped reproagent pipeline."""
    from finaince.reproduction import reproduce_report

    result = reproduce_report(Path(pdf_path), settings=settings)
    if result is None:
        return {"status": "empty", "pdf_path": str(pdf_path)}
    return dict(result)


def handle_validate_expression(expression: str) -> dict[str, Any]:
    """Expression validation via the shipped reproagent polars engine."""
    from finaince.reproduction import validate_expression

    payload = validate_expression(expression)
    payload["expression"] = expression
    return payload


def handle_search_library(
    query: str = "",
    style: str | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Catalog-first search (shared with CLI / FastMCP)."""
    from finaince.tools import handle_search_library as _shared

    return _shared(query=query, style=style, settings=settings)


@tool(
    "cull_factor_pool",
    "Cull a candidate factor pool with aiminer IC-threshold and correlation filters.",
    {"factors": list},
)
async def cull_factor_pool_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _json_content(handle_cull_factor_pool(list(args.get("factors") or [])))


@tool(
    "score_factor",
    "Score a factor/strategy using the shipped aiminer selection_score.",
    {"metrics": dict, "factor_ic": float},
)
async def score_factor_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _json_content(
        handle_score_factor(
            dict(args.get("metrics") or {}),
            factor_ic=float(args.get("factor_ic") or 0.0),
        )
    )


@tool(
    "reproduce_report",
    "Reproduce a research-report PDF through the shipped reproagent pipeline.",
    {"pdf_path": str},
)
async def reproduce_report_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _json_content(handle_reproduce_report(str(args.get("pdf_path") or "")))


@tool(
    "validate_expression",
    "Validate a factor expression against the shipped operator/field whitelist.",
    {"expression": str},
)
async def validate_expression_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _json_content(handle_validate_expression(str(args.get("expression") or "")))


@tool(
    "search_library",
    "Search the shipped reproagent factor library by name or style.",
    {"query": str, "style": str},
)
async def search_library_tool(args: dict[str, Any]) -> dict[str, Any]:
    style = args.get("style") or None
    if style == "":
        style = None
    return _json_content(
        handle_search_library(query=str(args.get("query") or ""), style=style)
    )


@tool(
    "catalog_list",
    "List the unified FinAlpha catalog (discovery | reproduction).",
    {"query": str, "source": str},
)
async def catalog_list_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_catalog_list

    source = args.get("source") or None
    if source == "":
        source = None
    return _json_content(handle_catalog_list(query=str(args.get("query") or ""), source=source))


@tool(
    "eval_expression",
    "Validate then backtest a repro_polars expression on local fixture/data.",
    {"expression": str, "dialect": str},
)
async def eval_expression_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_eval_expression

    return _json_content(
        handle_eval_expression(
            str(args.get("expression") or ""),
            dialect=str(args.get("dialect") or "repro_polars"),
        )
    )


@tool(
    "promote_factor",
    "Submit a catalog row for review (does not write the other engine store).",
    {"catalog_id": str, "direction": str},
)
async def promote_factor_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_promote

    return _json_content(
        handle_promote(str(args.get("catalog_id") or ""), direction=str(args.get("direction") or "to_pool"))
    )


@tool(
    "review_approve",
    "Approve a pending promotion and write the other SoR if gates pass.",
    {"promotion_id": str},
)
async def review_approve_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_review_approve

    return _json_content(handle_review_approve(str(args.get("promotion_id") or "")))


@tool(
    "review_reject",
    "Reject a pending promotion and return the catalog row to candidate.",
    {"promotion_id": str},
)
async def review_reject_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_review_reject

    return _json_content(handle_review_reject(str(args.get("promotion_id") or "")))


@tool(
    "list_jobs",
    "List platform jobs (swarm / reproduce / eval).",
    {},
)
async def list_jobs_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_list_jobs

    return _json_content(handle_list_jobs())


@tool(
    "doctor",
    "Report FinAlpha home, catalog path, and provider mapping.",
    {},
)
async def doctor_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_doctor

    return _json_content(handle_doctor())


@tool(
    "discover_swarm",
    "Record and optionally run the shipped aiminer manager swarm as a platform job.",
    {"sync": bool},
)
async def discover_swarm_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_discover_swarm

    sync = args.get("sync")
    if sync is None:
        sync = True
    return _json_content(handle_discover_swarm(sync=bool(sync)))


CUSTOM_TOOLS = [
    cull_factor_pool_tool,
    score_factor_tool,
    reproduce_report_tool,
    validate_expression_tool,
    search_library_tool,
    catalog_list_tool,
    eval_expression_tool,
    promote_factor_tool,
    review_approve_tool,
    review_reject_tool,
    list_jobs_tool,
    doctor_tool,
    discover_swarm_tool,
]


def allowed_mcp_tool_names() -> list[str]:
    return [f"mcp__{MCP_SERVER_NAME}__{t.name}" for t in CUSTOM_TOOLS]


def create_finaince_mcp_server() -> dict[str, Any]:
    """In-process MCP server wrapping the custom domain tools."""
    return create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version=MCP_SERVER_VERSION,
        tools=CUSTOM_TOOLS,
    )


def _event_field(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def inspect_pre_tool_use(event: Any) -> dict[str, Any]:
    """Inspect a PreToolUse event and return allow/deny hook output.

    This is the Python callback body registered on HookMatcher; tests call
    it directly with representative payloads.
    """
    tool_name = str(_event_field(event, "tool_name") or "")
    tool_input = _event_field(event, "tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    deny_reason = None
    if tool_name in _DENIED_TOOL_NAMES or tool_name.split("__")[-1] in {"unsafe", "Bash"}:
        deny_reason = f"tool {tool_name!r} is not permitted on the finaince SDK session"
    elif tool_name.endswith("reproduce_report"):
        pdf_path = str(tool_input.get("pdf_path") or "").strip()
        if not pdf_path:
            deny_reason = "reproduce_report requires a non-empty pdf_path"
        else:
            import os

            explicit = (os.getenv("FINAINCE_PDF_ROOT") or "").strip()
            if explicit:
                from finaince.runtime import pdf_root

                root = pdf_root()
                if root.is_dir():
                    try:
                        Path(pdf_path).resolve().relative_to(root.resolve())
                    except ValueError:
                        deny_reason = "pdf_path must be under FINAINCE_PDF_ROOT"
    elif tool_name.endswith("promote_factor") and not str(tool_input.get("catalog_id") or "").strip():
        deny_reason = "promote_factor requires catalog_id"
    elif tool_name.endswith("review_approve") and not str(tool_input.get("promotion_id") or "").strip():
        deny_reason = "review_approve requires promotion_id"

    if deny_reason:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"allow {tool_name}",
            "additionalContext": f"tool_input_keys={sorted(tool_input)}",
        }
    }


async def pre_tool_use_hook(
    input_data: Any,
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """Claude Agent SDK PreToolUse callback."""
    return inspect_pre_tool_use(input_data)


def inspect_post_tool_use(event: Any) -> dict[str, Any]:
    """After a tool runs, attach a one-line catalog snapshot as context."""
    tool_name = str(_event_field(event, "tool_name") or "")
    hint = "next: catalog_list then promote only if IC and daily_returns exist"
    try:
        from finaince.catalog.store import FactorCatalog

        n = len(FactorCatalog().list())
        hint = f"catalog_size={n}; {hint}"
    except Exception:  # noqa: BLE001
        pass
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"after {tool_name}; {hint}",
        }
    }


async def post_tool_use_hook(
    input_data: Any,
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    return inspect_post_tool_use(input_data)


def _specialist_agents() -> dict[str, Any]:
    from claude_agent_sdk import AgentDefinition

    from finaince.agent_playbook import specialist_briefs

    allowed = allowed_mcp_tool_names()
    agents: dict[str, Any] = {}
    for name, (description, prompt) in specialist_briefs().items():
        agents[name] = AgentDefinition(
            description=description,
            prompt=prompt,
            tools=allowed,
        )
    return agents


def build_claude_agent_options(**overrides: Any) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions with MCP tools, playbook, specialists, hooks."""
    from finaince.agent_playbook import system_prompt

    server = create_finaince_mcp_server()
    pre = HookMatcher(matcher="*", hooks=[pre_tool_use_hook])
    post = HookMatcher(matcher="*", hooks=[post_tool_use_hook])
    kwargs: dict[str, Any] = {
        "mcp_servers": {MCP_SERVER_NAME: server},
        "allowed_tools": allowed_mcp_tool_names(),
        "system_prompt": system_prompt(),
        "agents": _specialist_agents(),
        "hooks": {"PreToolUse": [pre], "PostToolUse": [post]},
    }
    kwargs.update(overrides)
    return ClaudeAgentOptions(**kwargs)


def describe_session_options(options: ClaudeAgentOptions | None = None) -> dict[str, Any]:
    """Serializable view of tools + hook registration (no live model)."""
    opts = options if options is not None else build_claude_agent_options()
    servers: list[dict[str, Any]] = []
    raw_servers = opts.mcp_servers
    if isinstance(raw_servers, dict):
        for name, cfg in raw_servers.items():
            if isinstance(cfg, dict):
                servers.append(
                    {
                        "key": name,
                        "type": cfg.get("type"),
                        "name": cfg.get("name"),
                        "has_instance": cfg.get("instance") is not None,
                    }
                )
            else:
                servers.append({"key": name, "type": type(cfg).__name__})
    hooks = []
    for event, matchers in (opts.hooks or {}).items():
        for matcher in matchers:
            hooks.append(
                {
                    "event": event,
                    "matcher": matcher.matcher,
                    "hook_count": len(matcher.hooks or []),
                }
            )
    return {
        "mcp_server_name": MCP_SERVER_NAME,
        "mcp_servers": servers,
        "allowed_tools": list(opts.allowed_tools or []),
        "custom_tool_names": [t.name for t in CUSTOM_TOOLS],
        "hooks": hooks,
        "has_system_prompt": bool(opts.system_prompt),
        "specialists": sorted((opts.agents or {}).keys()),
        "product": "FinAlpha",
    }


def try_live_query(prompt: str) -> dict[str, Any]:
    """Attempt ``query()``. Missing CLI/credentials is an environment failure."""
    import asyncio

    from claude_agent_sdk import query

    async def _run() -> list[str]:
        messages: list[str] = []
        async for message in query(prompt=prompt, options=build_claude_agent_options()):
            messages.append(type(message).__name__)
        return messages

    try:
        messages = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    if not messages:
        return {"ok": False, "error_type": "EmptyQuery", "error": "query returned no messages"}
    return {"ok": True, "messages": messages}

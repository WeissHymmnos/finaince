"""Claude Agent SDK custom system extensions for finaince.

Domain operations are registered as in-process MCP tools via ``@tool`` +
``create_sdk_mcp_server``. A PreToolUse hook is attached on
``ClaudeAgentOptions``. Tool bodies call the merged discovery/reproduction
functions; they do not reimplement them.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

from finaince._swallow import log_swallowed

try:
    from claude_agent_sdk import (
        AgentDefinition,
        ClaudeAgentOptions,
        HookMatcher,
        create_sdk_mcp_server,
        tool,
    )

    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False

    class ClaudeAgentOptions:  # type: ignore[no-redef]
        def __init__(
            self,
            *,
            mcp_servers: dict[str, Any] | None = None,
            allowed_tools: list[str] | None = None,
            system_prompt: str | None = None,
            agents: dict[str, Any] | None = None,
            hooks: dict[str, list[Any]] | None = None,
            **kwargs: Any,
        ) -> None:
            self.mcp_servers = mcp_servers or {}
            self.allowed_tools = allowed_tools or []
            self.system_prompt = system_prompt or ""
            self.agents = agents or {}
            self.hooks = hooks or {}
            for k, v in kwargs.items():
                setattr(self, k, v)

    class HookMatcher:  # type: ignore[no-redef]
        def __init__(self, *, matcher: str = "*", hooks: list[Any] | None = None) -> None:
            self.matcher = matcher
            self.hooks = hooks or []

    class AgentDefinition:  # type: ignore[no-redef]
        def __init__(
            self,
            *,
            description: str = "",
            prompt: str = "",
            tools: list[str] | None = None,
        ) -> None:
            self.description = description
            self.prompt = prompt
            self.tools = tools or []

    def create_sdk_mcp_server(  # type: ignore[no-redef]
        *,
        name: str = "",
        version: str = "",
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "sdk",
            "name": name,
            "version": version,
            "tools": tools or [],
            "instance": object(),
        }

    def tool(*_args: Any, **_kwargs: Any) -> Any:  # type: ignore[misc]
        def _wrap(fn: Any, name: str | None = None, description: str | None = None) -> Any:
            fn.name = name if name is not None else getattr(fn, "__name__", "")
            if description is not None:
                fn.description = description
            elif getattr(fn, "__doc__", None):
                fn.description = fn.__doc__
            else:
                fn.description = ""
            fn.handler = fn
            return fn

        if len(_args) == 1 and callable(_args[0]) and not _kwargs:
            return _wrap(_args[0])

        name = _kwargs.get("name") or (_args[0] if _args and isinstance(_args[0], str) else None)
        description = _kwargs.get("description") or (
            _args[1] if len(_args) > 1 and isinstance(_args[1], str) else None
        )

        def _decorator(fn: Any) -> Any:
            return _wrap(fn, name=name, description=description)

        return _decorator

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
    factors = list(args.get("factors") or [])
    res = await asyncio.to_thread(handle_cull_factor_pool, factors)
    return _json_content(res)


@tool(
    "score_factor",
    "Score a factor/strategy using the shipped aiminer selection_score.",
    {"metrics": dict, "factor_ic": float},
)
async def score_factor_tool(args: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(args.get("metrics") or {})
    factor_ic = float(args.get("factor_ic") or 0.0)
    res = await asyncio.to_thread(handle_score_factor, metrics, factor_ic=factor_ic)
    return _json_content(res)


@tool(
    "reproduce_report",
    "Reproduce a research-report PDF through the shipped reproagent pipeline.",
    {"pdf_path": str},
)
async def reproduce_report_tool(args: dict[str, Any]) -> dict[str, Any]:
    pdf_path = str(args.get("pdf_path") or "")
    res = await asyncio.to_thread(handle_reproduce_report, pdf_path)
    return _json_content(res)


@tool(
    "validate_expression",
    "Validate a factor expression against the shipped operator/field whitelist.",
    {"expression": str},
)
async def validate_expression_tool(args: dict[str, Any]) -> dict[str, Any]:
    expr = str(args.get("expression") or "")
    res = await asyncio.to_thread(handle_validate_expression, expr)
    return _json_content(res)


@tool(
    "search_library",
    "Search the shipped reproagent factor library by name or style.",
    {"query": str, "style": str},
)
async def search_library_tool(args: dict[str, Any]) -> dict[str, Any]:
    style = args.get("style") or None
    if style == "":
        style = None
    query = str(args.get("query") or "")
    res = await asyncio.to_thread(handle_search_library, query=query, style=style)
    return _json_content(res)


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
    query = str(args.get("query") or "")
    res = await asyncio.to_thread(handle_catalog_list, query=query, source=source)
    return _json_content(res)


@tool(
    "eval_expression",
    "Validate then backtest a repro_polars expression on local fixture/data.",
    {"expression": str, "dialect": str},
)
async def eval_expression_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_eval_expression

    expr = str(args.get("expression") or "")
    dialect = str(args.get("dialect") or "repro_polars")
    res = await asyncio.to_thread(handle_eval_expression, expr, dialect=dialect)
    return _json_content(res)


@tool(
    "promote_factor",
    "Submit a catalog row for review (does not write the other engine store).",
    {"catalog_id": str, "direction": str},
)
async def promote_factor_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_promote

    cat_id = str(args.get("catalog_id") or "")
    direction = str(args.get("direction") or "to_pool")
    res = await asyncio.to_thread(handle_promote, cat_id, direction=direction)
    return _json_content(res)


@tool(
    "review_approve",
    "Approve a pending promotion and write the other SoR if gates pass.",
    {"promotion_id": str},
)
async def review_approve_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_review_approve

    promo_id = str(args.get("promotion_id") or "")
    res = await asyncio.to_thread(handle_review_approve, promo_id)
    return _json_content(res)


@tool(
    "review_reject",
    "Reject a pending promotion and return the catalog row to candidate.",
    {"promotion_id": str},
)
async def review_reject_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_review_reject

    promo_id = str(args.get("promotion_id") or "")
    res = await asyncio.to_thread(handle_review_reject, promo_id)
    return _json_content(res)


@tool(
    "list_jobs",
    "List platform jobs (swarm / reproduce / eval).",
    {},
)
async def list_jobs_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_list_jobs

    res = await asyncio.to_thread(handle_list_jobs)
    return _json_content(res)


@tool(
    "doctor",
    "Report FinAlpha home, catalog path, and provider mapping.",
    {},
)
async def doctor_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_doctor

    res = await asyncio.to_thread(handle_doctor)
    return _json_content(res)


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
    res = await asyncio.to_thread(handle_discover_swarm, sync=bool(sync))
    return _json_content(res)


@tool(
    "recent_failures",
    "Retrieve recent failed trace events by error prefix before re-drafting an impl.",
    {"error": str, "limit": int},
)
async def recent_failures_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_recent_failures

    error = args.get("error")
    if error == "":
        error = None
    limit = args.get("limit")
    if limit is None:
        limit = 5
    res = await asyncio.to_thread(handle_recent_failures, error=error, limit=int(limit))
    return _json_content(res)


@tool(
    "research_context",
    "Diverse low-correlation factor samples + recent failure lessons to ground the next proposal.",
    {"error_prefix": str, "sample_limit": int, "lesson_limit": int},
)
async def research_context_tool(args: dict[str, Any]) -> dict[str, Any]:
    from finaince.tools import handle_research_context

    error_prefix = args.get("error_prefix")
    if error_prefix == "":
        error_prefix = None
    sample_limit = args.get("sample_limit")
    if sample_limit is None:
        sample_limit = 5
    lesson_limit = args.get("lesson_limit")
    if lesson_limit is None:
        lesson_limit = 5
    res = await asyncio.to_thread(
        handle_research_context,
        error_prefix=error_prefix,
        sample_limit=int(sample_limit),
        lesson_limit=int(lesson_limit),
    )
    return _json_content(res)


CUSTOM_TOOLS = [
    recent_failures_tool,
    research_context_tool,
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
    except Exception as _exc:  # noqa: BLE001
        log_swallowed("finaince.sdk_ext:498", _exc)
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
        "available": _HAS_SDK,
    }


def _safe_run_async(target: Any) -> Any:
    """Safely execute an async coroutine or coroutine factory without RuntimeError when called in a running loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _execute() -> Any:
        coro = target() if (inspect.iscoroutinefunction(target) or callable(target)) else target
        return asyncio.run(coro)

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_execute).result()
    else:
        return _execute()


def try_live_query(prompt: str) -> dict[str, Any]:
    """Attempt ``query()``. Missing CLI/credentials is an environment failure."""
    if not _HAS_SDK:
        return {
            "ok": False,
            "error_type": "ModuleNotFoundError",
            "error": "claude_agent_sdk is not installed",
        }

    from claude_agent_sdk import query

    async def _run() -> list[str]:
        messages: list[str] = []
        async for message in query(prompt=prompt, options=build_claude_agent_options()):
            messages.append(type(message).__name__)
        return messages

    try:
        messages = _safe_run_async(_run)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    if not messages:
        return {"ok": False, "error_type": "EmptyQuery", "error": "query returned no messages"}
    return {"ok": True, "messages": messages}

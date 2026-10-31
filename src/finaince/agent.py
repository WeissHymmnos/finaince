"""Run the FinAlpha research desk through Claude Agent SDK ``query()``."""

from __future__ import annotations

from typing import Any

from finaince.sdk_ext import _safe_run_async, build_claude_agent_options


def run_research_desk(prompt: str, *, max_turns: int | None = 16) -> dict[str, Any]:
    """One-shot agent turn. Missing claude CLI is an environment failure."""
    try:
        from claude_agent_sdk import ResultMessage, TextBlock, query
    except ImportError as exc:
        return {
            "ok": False, "desk": "FinAlpha", "prompt": prompt,
            "error_type": type(exc).__name__, "error": str(exc),
        }

    options = build_claude_agent_options(**({"max_turns": max_turns} if max_turns is not None else {}))

    texts: list[str] = []
    kinds: list[str] = []
    result_error: dict[str, Any] = {}

    async def _run() -> None:
        async for message in query(prompt=prompt, options=options):
            kinds.append(type(message).__name__)
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock) and block.text:
                        texts.append(block.text)
                    elif getattr(block, "text", None):
                        texts.append(str(block.text))
            if isinstance(message, ResultMessage):
                if getattr(message, "result", None):
                    texts.append(str(message.result))
                if getattr(message, "is_error", False):
                    result_error.update(
                        error_type=getattr(message, "subtype", "QueryError"),
                        error=str(getattr(message, "result", None) or "research desk query failed"),
                    )

    try:
        _safe_run_async(_run)
    except Exception as exc:  # noqa: BLE001
        # A streamed session may already have written catalog/jobs state. Replaying
        # the entire prompt here would execute those side effects a second time.
        result_error.update(error_type=type(exc).__name__, error=str(exc))
    if result_error:
        return {
            "ok": False, "desk": "FinAlpha", "prompt": prompt,
            "messages": kinds, "text": "\n".join(texts).strip(), **result_error,
        }
    if not kinds:
        return {"ok": False, "error_type": "EmptyQuery", "error": "query returned no messages", "prompt": prompt}
    return {
        "ok": True,
        "desk": "FinAlpha",
        "prompt": prompt,
        "messages": kinds,
        "text": "\n".join(texts).strip(),
    }

"""Run the FinAlpha research desk through Claude Agent SDK ``query()``."""

from __future__ import annotations

from typing import Any

from finaince.sdk_ext import build_claude_agent_options, try_live_query


def run_research_desk(prompt: str, *, max_turns: int | None = 16) -> dict[str, Any]:
    """One-shot agent turn. Missing claude CLI is an environment failure."""
    import asyncio

    from claude_agent_sdk import ResultMessage, TextBlock, query

    options = build_claude_agent_options()
    if max_turns is not None:
        options = build_claude_agent_options(max_turns=max_turns)

    texts: list[str] = []
    kinds: list[str] = []

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
            if isinstance(message, ResultMessage) and getattr(message, "result", None):
                texts.append(str(message.result))

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        fallback = try_live_query(prompt)
        fallback["prompt"] = prompt
        fallback["desk"] = "FinAlpha"
        if not fallback.get("ok"):
            fallback["error_type"] = type(exc).__name__
            fallback["error"] = str(exc)
        return fallback
    if not kinds:
        return {"ok": False, "error_type": "EmptyQuery", "error": "query returned no messages", "prompt": prompt}
    return {
        "ok": True,
        "desk": "FinAlpha",
        "prompt": prompt,
        "messages": kinds,
        "text": "\n".join(texts).strip(),
    }

"""Best-effort dialect translation using operators.yaml."""

from __future__ import annotations

import re
from pathlib import Path

from finaince.eval.router import is_listed, listed_operators


_QLIB_FIELD = re.compile(r"\$(close|open|high|low|volume|amount|vwap)\b", re.I)


def translate_from_qlib(expression: str) -> str:
    """Map `$close` style fields onto the polars/repro names the local engine accepts."""
    return _QLIB_FIELD.sub(lambda match: match.group(1).lower(), expression)


def translate_to_qlib(expression: str) -> str | None:
    if not expression or not is_listed(expression):
        return None
    text = expression
    text = re.sub(r"\bclose\b", "$close", text)
    text = re.sub(r"\bopen\b", "$open", text)
    text = re.sub(r"\bhigh\b", "$high", text)
    text = re.sub(r"\blow\b", "$low", text)
    text = re.sub(r"\bvolume\b", "$volume", text)
    text = re.sub(r"\bamount\b", "$amount", text)
    text = text.replace("Delta(", "Delta(")
    return text


def attach_translation(expression: str, dialect: str) -> dict[str, object]:
    translatable = is_listed(expression)
    alt = translate_to_qlib(expression) if dialect == "repro_polars" and translatable else None
    return {
        "translatable": translatable,
        "alt_text": alt,
        "operators": sorted(listed_operators()),
    }

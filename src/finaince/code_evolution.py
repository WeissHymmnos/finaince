"""WS-H governance-native code-factor evolution (CoSTEER-lite).

Loop per plan: LLM writes a complete ``compute(panel)`` -> frozen sandbox child
(:mod:`finaince.isolate`) -> shipped eval + fail-closed gates -> on failure the
next draft is rewritten from (error-prefix failure retrieval + AST-diff motive
of the previous source). Every accepted artifact lands in the catalog with its
full audit chain; nothing is auto-promoted past the gates.

Hermetic honesty: when no chat provider is configured the rewrite step stops
with ``stage="llm_unavailable"`` instead of pretending to iterate.
"""

from __future__ import annotations

import difflib
import json
import os
import textwrap
from typing import Any

SEED_TEMPLATE = '''\
NAME = "{name}"
EXPRESSION = "{expression}"

def compute(panel):
    close = list(panel["close"])
    window = {window}
    values = []
    for i in range(len(close)):
        start = max(0, i - window)
        segment = close[start : i + 1]
        first = segment[0]
        values.append(sum(segment) / len(segment) - first)
    return values
'''


def default_seed(name: str = "code_evo", expression: str = "", window: int = 5) -> str:
    return SEED_TEMPLATE.format(
        name=name,
        expression=expression or "compute(panel)",
        window=max(2, int(window)),
    )


def ast_edit_motive(previous: str, current: str) -> dict[str, Any]:
    """Structural summary of an edit between two drafts (WS-L memory input)."""
    prev_lines = previous.splitlines()
    cur_lines = current.splitlines()
    differ = difflib.SequenceMatcher(a=prev_lines, b=cur_lines)
    added = removed = changed_regions = 0
    signatures_equal = False

    def _signature(lines: list[str]) -> str:
        import ast as pyast

        try:
            tree = pyast.parse("\n".join(lines))
        except SyntaxError:
            return ""
        functions = [n for n in tree.body if isinstance(n, pyast.FunctionDef)]
        return json.dumps([ast_dump_signature(fn) for fn in functions], sort_keys=True)

    try:
        signatures_equal = _signature(prev_lines) == _signature(cur_lines)
    except Exception:
        signatures_equal = False
    for tag, _i1, _i2, j1, j2 in differ.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += j2 - j1
        elif tag == "replace":
            added += j2 - j1
            removed += j2 - j1
            changed_regions += 1
    return {
        "added_lines": added,
        "removed_lines": removed,
        "changed_regions": changed_regions,
        "signatures_equal": signatures_equal,
    }


def ast_dump_signature(fn: Any) -> dict[str, Any]:
    import ast as pyast

    body_ops = [
        type(node).__name__
        for node in pyast.walk(fn)
        if isinstance(node, (pyast.Call, pyast.For, pyast.If, pyast.While, pyast.ListComp))
    ]
    return {
        "name": fn.name,
        "args": [a.arg for a in fn.args.args],
        "body_ops": sorted(body_ops),
    }


def build_rewrite_prompt(
    hypothesis: str,
    *,
    previous_source: str,
    error: str,
    lessons: list[dict[str, Any]],
) -> str:
    prompt = (
        "You are repairing a quantitative factor implementation.\n"
        f"Hypothesis: {hypothesis}\n\n"
        "Constraints:\n"
        "- define NAME and a function compute(panel) -> numeric list\n"
        "- panel is a dict of column lists (e.g. panel['close'])\n"
        "- imports limited to math/statistics/json/datetime/collections/itertools/"
        "functools/operator/decimal/typing/numpy/pandas/polars\n"
        "- no file IO, no network, no exec/eval/open/subprocess\n\n"
        f"Previous source:\n{textwrap.indent(previous_source, '    ')}\n\n"
        f"Failure: {error}\n"
    )
    if lessons:
        prompt += "\nSimilar past failures:\n"
        for lesson in lessons[:3]:
            prompt += f"- {lesson.get('error')}: {(lesson.get('summary') or '')[:160]}\n"
    prompt += "\nReturn ONLY the complete corrected Python source."
    return prompt


def _chat_complete(prompt: str) -> str | None:
    from finaince.runtime import resolve_llm

    llm = resolve_llm() or {}
    base_url = str(llm.get("base_url") or "")
    model = str(llm.get("model") or "")
    api_key = llm.get("api_key")
    if not base_url or not model or not api_key:
        return None
    try:
        import httpx

        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return str(content) if content else None
    except Exception:
        return None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = [line for line in lines[1:] if not line.strip().startswith("```")]
        return "\n".join(body).strip()
    return stripped


def evolve_code_factor(
    hypothesis: str,
    *,
    seed_source: str | None = None,
    name: str = "code_evolution",
    expression: str = "",
    rounds: int = 3,
    universe: str = "local_panel",
) -> dict[str, Any]:
    """Run the governed evolution loop. Honest at every exit."""
    from finaince.isolate import run_isolated, similar_errors, upsert_isolated

    drafts: list[dict[str, Any]] = []
    source = seed_source or default_seed(name=name, expression=expression)
    last_error: str | None = None
    total_rounds = max(1, int(rounds))

    for index in range(total_rounds):
        motive = ast_edit_motive(drafts[-1]["source"], source) if drafts else None
        result = run_isolated(source, name=f"{name}_r{index}", expression=expression or None)
        ok = bool(result.get("ok"))
        drafts.append(
            {
                "round": index,
                "ok": ok,
                "error": None if ok else str(result.get("error")),
                "via": result.get("via"),
                "similar_errors": result.get("similar_errors") or [],
                "edit_motive": motive,
                "source": source,
            }
        )
        if not ok:
            last_error = str(result.get("error"))
            prompt = build_rewrite_prompt(
                hypothesis,
                previous_source=source,
                error=last_error,
                lessons=similar_errors(last_error),
            )
            rewritten = _chat_complete(prompt)
            if not rewritten:
                mocked = os.environ.get("ALLOW_MOCK_LLM") == "true"
                return {
                    "ok": False,
                    "stage": "no_rewrite" if mocked else "llm_unavailable",
                    "error": last_error,
                    "rounds_attempted": index + 1,
                    "drafts": drafts,
                    "prompt_preview": prompt[:400],
                }
            source = _strip_code_fence(rewritten)
            continue

        stored = upsert_isolated(result, universe=universe)
        if not stored.get("ok"):
            return {
                "ok": False,
                "stage": "catalog_upsert_failed",
                "error": stored.get("error"),
                "drafts": drafts,
            }
        record = stored["record"]
        from finaince.review.gates import evaluate_gates

        gates = evaluate_gates(record, direction="to_pool")
        try:
            from finaince.trace import append_event

            append_event(
                "code_evolution",
                metrics={
                    "ok": True,
                    "name": name,
                    "catalog_id": stored.get("catalog_id"),
                    "gates_passed": gates.get("passed"),
                    "failures": gates.get("failures"),
                    "rounds": len(drafts),
                },
                hypothesis=hypothesis,
                extra={"draft_errors": [d["error"] for d in drafts]},
            )
        except Exception:
            pass
        return {
            "ok": True,
            "stage": "governed",
            "catalog_id": stored.get("catalog_id"),
            "gates": gates,
            "rounds_used": len(drafts),
            "drafts": [
                {k: v for k, v in d.items() if k != "source"} for d in drafts
            ],
            "final_via": result.get("via"),
        }
    return {
        "ok": False,
        "stage": "rounds_exhausted",
        "error": last_error,
        "rounds_attempted": total_rounds,
        "drafts": [{k: v for k, v in d.items() if k != "source"} for d in drafts],
    }

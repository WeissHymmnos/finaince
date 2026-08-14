"""Map extract / translate outcomes to no_factors vs needs_impl vs runnable."""

from __future__ import annotations

from typing import Any


def classify_research_outcome(
    *,
    factor_count: int = 0,
    described: bool = False,
    expression: str | None = None,
    dialect: str = "repro_polars",
    status: str | None = None,
) -> str:
    """Empty extract → no_factors. Described but not runnable as dialect → needs_impl."""
    if expression is not None:
        text = expression.strip()
        if not text:
            return "no_factors"
        from finaince.eval.dialects import attach_translation

        trans = attach_translation(text, dialect)
        if not trans.get("translatable"):
            return "needs_impl"
        return "runnable"
    if status == "no_factors" or int(factor_count or 0) <= 0:
        return "needs_impl" if described else "no_factors"
    return str(status or "runnable")


def annotate_reproduce(result: dict[str, Any] | None) -> dict[str, Any]:
    """Stamp impl_status onto a shipped pipeline result. Empty stays no_factors."""
    out = dict(result or {})
    factors = out.get("factors") or out.get("factor_results") or []
    count = int(out.get("factor_count") if out.get("factor_count") is not None else len(factors))
    described = False
    untranslatable = False
    for item in factors:
        if not isinstance(item, dict):
            continue
        formula = str(item.get("formula") or item.get("expression") or "")
        if item.get("description") or item.get("name") or formula:
            described = True
        if formula:
            from finaince.eval.dialects import attach_translation

            if not attach_translation(formula, "repro_polars").get("translatable"):
                untranslatable = True
    status = str(out.get("status") or out.get("overall") or "")
    if status == "no_factors" or count <= 0:
        impl = "needs_impl" if (described or untranslatable) else "no_factors"
    elif untranslatable:
        impl = "needs_impl"
    else:
        impl = classify_research_outcome(factor_count=count, described=described, status=status)
    out["impl_status"] = impl
    if impl == "needs_impl" and status == "no_factors":
        # keep pipeline honesty for empty extract; only rewrite when something was described
        if described or untranslatable:
            out["status"] = "needs_impl"
    return out

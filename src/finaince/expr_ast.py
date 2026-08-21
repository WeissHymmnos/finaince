"""Expression AST: structural originality and complexity for generation-side regularization.

WS-A (docs/improvement-plan-v3.md): both expression dialects (qlib, repro_polars)
are function-call shaped; after stripping ``$`` prefixes they parse with the
stdlib :mod:`ast`. This module produces a normalized operator tree so that

- near-duplicate candidates can be blocked *before* evaluation cost is paid,
- complexity can be bounded before an LLM burns a simulation slot.

Fail-closed discipline: :func:`parse` raises ValueError on anything it cannot
understand; callers must treat unparseable expressions as "not regularizable"
and let the existing regex-level validation decide.
"""

from __future__ import annotations

import ast as pyast
import bisect
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "OpNode",
    "complexity",
    "expr_hash",
    "max_similarity_vs",
    "normalize",
    "parse",
    "serialize",
    "similarity",
]


# Operators whose argument order carries no meaning (Corr/Mul/Max/Min/Add 类).
_COMMUTATIVE = frozenset({"corr", "mul", "max", "min", "add"})

# f(f(x)) == f(x) — folded during normalization.
_IDEMPOTENT_UNARY = frozenset({"rank", "csrank", "abs", "sign"})

# Bucketing feeds only the O(1) expr_hash dedup key; similarity keeps raw values
# so that window 1 vs 2 stays distinguishable (acceptance test depends on it).
_WINDOW_BUCKETS = (1, 5, 10, 20, 60, 120, 240)


@dataclass(frozen=True)
class OpNode:
    """Normalized operator-tree node.

    op="__field__" leaves carry the field name in ``params``;
    op="__const__" leaves carry a numeric literal in ``params``.
    """

    op: str
    params: tuple = ()
    children: tuple[OpNode, ...] = ()

    @property
    def size(self) -> int:
        return 1 + sum(child.size for child in self.children) + len(self.params)


def _strip_dialect_marks(text: str) -> str:
    return text.replace("$", "").replace("|", "")


def _bucket(value: float) -> int:
    midpoints = [(a + b) / 2.0 for a, b in zip(_WINDOW_BUCKETS, _WINDOW_BUCKETS[1:])]
    index = bisect.bisect_right(midpoints, value)
    return _WINDOW_BUCKETS[index]


def _to_node(tree: pyast.AST) -> OpNode:
    if isinstance(tree, pyast.Expression):
        return _to_node(tree.body)
    if isinstance(tree, pyast.Name):
        name = tree.id.lower()
        return OpNode(op="__field__", params=(name,))
    if isinstance(tree, pyast.Constant):
        value = tree.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"unsupported constant: {value!r}")
        num = float(value)
        if num != num:
            raise ValueError("NaN constant")
        return OpNode(op="__const__", params=(num,))
    if isinstance(tree, pyast.Call):
        func = tree.func
        fname = getattr(func, "id", None) or getattr(func, "attr", None)
        if not isinstance(fname, str):
            raise ValueError(f"unsupported call target: {pyast.dump(func)[:80]}")
        op = fname.lower()
        children: list[OpNode] = []
        params: list[float] = []
        for arg in tree.args:
            node = _to_node(arg)
            if node.op == "__const__":
                params.append(node.params[0])
            else:
                children.append(node)
        for kw in tree.keywords:
            raise ValueError(f"keyword arguments unsupported: {kw.arg}")
        return OpNode(op=op, params=tuple(params), children=tuple(children))
    if isinstance(tree, pyast.BinOp):
        op_map = {pyast.Add: "add", pyast.Sub: "sub", pyast.Mult: "mul", pyast.Div: "div", pyast.Pow: "pow"}
        op_cls = type(tree.op)
        if op_cls not in op_map:
            raise ValueError(f"unsupported binary operator: {op_cls.__name__}")
        return OpNode(
            op=op_map[op_cls],
            children=(_to_node(tree.left), _to_node(tree.right)),
        )
    if isinstance(tree, pyast.UnaryOp):
        if isinstance(tree.op, pyast.USub):
            return OpNode(op="neg", children=(_to_node(tree.operand),))
        if isinstance(tree.op, pyast.UAdd):
            return _to_node(tree.operand)
        raise ValueError(f"unsupported unary operator: {type(tree.op).__name__}")
    raise ValueError(f"unsupported syntax: {type(tree).__name__}")


def parse(text: str, dialect: str) -> OpNode:
    """Parse an expression in either function-call dialect into an OpNode tree.

    Raises ValueError when the text cannot be understood; never guesses.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty expression")
    cleaned = _strip_dialect_marks(text.strip())
    try:
        parsed = pyast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"unparseable expression ({dialect}): {exc.msg}") from exc
    return _to_node(parsed)


# --------------------------------------------------------------------------- #
# Normalization / serialization / hashing
# --------------------------------------------------------------------------- #


def normalize(node: OpNode) -> OpNode:
    """Canonical form: fold idempotent wrappers, sort commutative children."""
    children = tuple(normalize(child) for child in node.children)
    params = node.params
    if node.op in _IDEMPOTENT_UNARY and len(children) == 1 and children[0].op == node.op:
        inner = children[0]
        return OpNode(op=node.op, params=params, children=inner.children)
    if node.op in _COMMUTATIVE:
        children = tuple(sorted(children, key=serialize))
    return OpNode(op=node.op, params=params, children=children)


def serialize(node: OpNode) -> str:
    canon = normalize(node)
    params = ",".join(repr(p) for p in canon.params)
    kids = "|".join(serialize(c) for c in canon.children)
    return f"{canon.op}({params})[{kids}]"


def expr_hash(node_or_text: OpNode | str, dialect: str = "repro_polars") -> str:
    """Stable hash over the normalized tree with coarse-bucketed windows."""
    node = parse(node_or_text, dialect) if isinstance(node_or_text, str) else node_or_text
    canon = normalize(node)

    def _coarse(n: OpNode) -> str:
        params = ",".join(str(_bucket(float(p))) if n.op != "__field__" else str(p) for p in n.params)
        kids = "|".join(_coarse(c) for c in n.children)
        return f"{n.op}({params})[{kids}]"

    return hashlib.sha256(_coarse(canon).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Similarity (AlphaAgent-style subtree isomorphism, subtree-hash accelerated)
# --------------------------------------------------------------------------- #


def _subtree_index(node: OpNode) -> dict[str, int]:
    """Map serialized subtree -> max node count seen for it."""
    index: dict[str, int] = {}
    stack = [node]
    while stack:
        cur = stack.pop()
        key = serialize(cur)
        prev = index.get(key, 0)
        index[key] = max(prev, cur.size)
        stack.extend(cur.children)
    return index


def _aligned_score(a: OpNode, b: OpNode) -> float:
    """Top-down match score; parameter differences cost the leaf, structure does not."""
    if a.op != b.op:
        return 0.0
    if a.op == "__field__":
        return 1.0 if a.params == b.params else 0.0
    if len(a.children) != len(b.children):
        return 0.0
    score = 1.0
    if a.params == b.params:
        score += len(a.params)
    for ca, cb in zip(a.children, b.children):
        score += _aligned_score(ca, cb)
    return score


def similarity(a: OpNode, b: OpNode) -> float:
    """Structural similarity in [0, 1].

    Max of (exact common-subtree Dice ratio, top-down aligned ratio that
    tolerates differing window parameters).
    """
    total = max(a.size, b.size)
    if total == 0:
        return 1.0
    sa, sb = serialize(a), serialize(b)
    if sa == sb:
        return 1.0
    index_b = _subtree_index(b)
    best_common = 0
    stack = [a]
    while stack:
        cur = stack.pop()
        hit = index_b.get(serialize(cur))
        if hit:
            best_common = max(best_common, min(hit, cur.size))
        stack.extend(cur.children)
    dice = 2.0 * best_common / (a.size + b.size)
    aligned = _aligned_score(normalize(a), normalize(b)) / total
    return max(dice, aligned)


def complexity(node: OpNode) -> dict[str, int]:
    """{"sl": symbol length (nodes), "pc": free numeric params, "fc": distinct fields}."""

    def _walk(n: OpNode, fields: set[str], pc_box: list[int]) -> None:
        if n.op == "__field__":
            fields.add(str(n.params[0]))
        elif n.op == "__const__":
            pc_box[0] += 1
        else:
            pc_box[0] += sum(1 for p in n.params if isinstance(p, (int, float)))
        for child in n.children:
            _walk(child, fields, pc_box)

    fields: set[str] = set()
    pc_box = [0]
    _walk(node, fields, pc_box)
    return {"sl": node.size, "pc": pc_box[0], "fc": len(fields)}


def max_similarity_vs(
    text: str,
    dialect: str,
    corpus: Iterable[tuple[str, str]],
) -> float:
    """Max structural similarity of ``text`` against a corpus of (id, expr) pairs.

    Unparseable corpus rows are skipped honestly; an empty effective corpus
    returns 0.0 (nothing known to be similar to).
    """
    probe = parse(text, dialect)
    best = 0.0
    for _cid, expr in corpus:
        try:
            other = parse(expr, dialect)
        except ValueError:
            continue
        sim = similarity(probe, other)
        best = max(best, sim)
    return best

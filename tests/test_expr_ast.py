"""WS-A AST originality/complexity + WS-C sign-reflection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finaince.expr_ast import (
    complexity,
    expr_hash,
    max_similarity_vs,
    normalize,
    parse,
    serialize,
    similarity,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _zoo() -> list[tuple[str, str]]:
    data = json.loads((FIXTURES / "alpha_seed_zoo.json").read_text())
    return [(s["id"], s["expr"]) for s in data["seeds"]]


def test_parse_both_dialects_function_calls() -> None:
    qlib = parse("Rank(Delta($close, 20))", "qlib")
    pl = parse("Rank(Delta(close, 20))", "repro_polars")
    assert qlib.op == "rank"
    assert qlib.children[0].op == "delta"
    assert pl.children[0].children[0] == ("__field__", ("close",), ()) or pl.children[0].children[0].op == "__field__"
    assert qlib.children[0].children[0].params == ("close",)


def test_parse_binop_and_unary() -> None:
    tree = parse("$close / Mean($close, 20) - 1", "qlib")
    assert tree.op == "sub"
    assert tree.children[0].op == "div"
    neg = parse("-Rank(Delta(close, 5))", "repro_polars")
    assert neg.op == "neg"


def test_parse_rejects_garbage_fail_closed() -> None:
    with pytest.raises(ValueError):
        parse("Rank((", "repro_polars")
    with pytest.raises(ValueError):
        parse("", "qlib")
    with pytest.raises(ValueError):
        parse("f'{x}'", "repro_polars")


def test_normalization_idempotent_and_commutative() -> None:
    a = normalize(parse("Corr(close, volume, 20)", "repro_polars"))
    b = normalize(parse("Corr(volume, close, 20)", "repro_polars"))
    assert serialize(a) == serialize(b)
    assert serialize(normalize(a)) == serialize(a)
    folded = normalize(parse("Rank(Rank(close))", "repro_polars"))
    assert folded.children[0].op == "__field__"


def test_expr_hash_stable_across_equivalent_forms() -> None:
    h1 = expr_hash("Corr($close, $volume, 19)", "qlib")
    h2 = expr_hash("corr(volume, close, 21)", "repro_polars")
    assert h1 == h2
    h3 = expr_hash("Rank(Delta(close, 5))", "repro_polars")
    h4 = expr_hash("Rank(Min(low, 60))", "repro_polars")
    assert h3 != h4


def test_similarity_ordering() -> None:
    self_tree = parse("Rank(Delta(close, 20))", "repro_polars")
    assert similarity(self_tree, self_tree) == 1.0
    window_variant = parse("Rank(Delta(close, 10))", "repro_polars")
    unrelated = parse("Sum(volume, 60)", "repro_polars")
    sim_variant = similarity(self_tree, window_variant)
    sim_unrelated = similarity(self_tree, unrelated)
    assert 0.5 < sim_variant < 1.0
    assert sim_unrelated < 0.34


def test_complexity_counts() -> None:
    cx = complexity(parse("If(Greater(volume, Mean(volume, 20)), Rank(Delta(close, 5)), Rank(-Delta(close, 5)))", "repro_polars"))
    assert cx["fc"] == 2
    assert cx["pc"] == 3
    deep = "Rank(" * 9 + "close" + ", 5)" * 9
    cx_deep = complexity(parse(deep, "repro_polars"))
    assert cx_deep["sl"] > 15


def test_max_similarity_vs_skips_unparseable_honestly() -> None:
    corpus = [("bad", "not parseable (("), ("good", "Rank(Delta(close, 20))")]
    sim = max_similarity_vs("Rank(Delta(close, 20))", "repro_polars", corpus)
    assert sim == 1.0


def test_seed_zoo_all_parse() -> None:
    data = json.loads((FIXTURES / "alpha_seed_zoo.json").read_text())
    assert len(data["seeds"]) >= 30
    for seed in data["seeds"]:
        parse(seed["expr"], seed["dialect"])


def _record(expr: str, dialect: str = "repro_polars"):
    from datetime import UTC, datetime

    from finaince.domain.factor import FactorExpression, FactorLineage, FactorRecord

    return FactorRecord(
        id="test_originality",
        name="t",
        name_cn=None,
        expression=FactorExpression(dialect=dialect, text=expr),
        lineage=FactorLineage(source="manual", source_ref="t-1"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        daily_returns={f"2024-01-{d:02d}": d / 100.0 for d in range(1, 25)},
    )


def test_homogeneous_gate_blocks_exact_duplicate(monkeypatch) -> None:
    from finaince.review.gates import homogeneous_gate

    monkeypatch.setattr(
        "finaince.review.gates._originality_corpus",
        lambda exclude_id=None: [("mom_01", "Rank(Delta(close, 20))")],
    )
    verdict = homogeneous_gate(_record("Rank(Delta(close, 20))"))
    assert verdict["failure"] == "homogeneous"
    verdict_ok = homogeneous_gate(_record("Sum(volume, 60)"))
    assert verdict_ok["failure"] is None
    assert verdict_ok["detail"].startswith("max_sim=")


def test_overcomplex_gate_thresholds() -> None:
    from finaince.review.gates import overcomplex_gate

    ok = overcomplex_gate(_record("Rank(Delta(close, 20))"))
    assert ok["failure"] is None
    deep = "Rank(" * 8 + "Div(close, Mean(close, 5))" + ", 5)" * 8
    bad = overcomplex_gate(_record(deep))
    assert bad["failure"] == "overcomplex"


def test_evaluate_gates_override_paths(monkeypatch) -> None:
    from finaince.review import gates as gates_mod

    record = _record("Rank(Delta(close, 20))")
    monkeypatch.setattr(gates_mod, "_originality_corpus", lambda exclude_id=None: [("mom_01", "Rank(Delta(close, 20))")])
    blocked = gates_mod.evaluate_gates(record, direction="to_pool", override=["thin_panel"])
    assert "homogeneous" in blocked["failures"]
    allowed = gates_mod.evaluate_gates(record, direction="to_pool", override=["thin_panel", "homogeneous"])
    assert "homogeneous" not in allowed["failures"]


def test_reflect_sign_math() -> None:
    from finaince.discovery import reflect_sign

    n = 40
    metrics = {
        "ic": -0.05,
        "rank_ic": -0.04,
        "sharpe": -1.2,
        "ic_ir": -0.6,
        "turnover": 0.35,
        "max_drawdown": -0.12,
        "hypothesis": "neg-mom",
        "returns": {f"2024-01-{d:02d}": d / 100.0 for d in range(1, n + 1)},
    }
    mirror = reflect_sign(metrics)
    assert mirror is not None
    assert mirror["ic"] == pytest.approx(0.05)
    assert mirror["rank_ic"] == pytest.approx(0.04)
    assert abs(mirror["sharpe"]) == pytest.approx(abs(metrics["sharpe"]))
    assert abs(mirror["ic_ir"]) == pytest.approx(abs(metrics["ic_ir"]))
    assert mirror["turnover"] == metrics["turnover"]
    assert mirror["max_drawdown"] == metrics["max_drawdown"]
    assert mirror["sign_reflected"] is True
    assert "[sign-mirrored]" in mirror["hypothesis"]


def test_reflect_sign_none_when_not_justified() -> None:
    from finaince.discovery import reflect_sign

    base = {"returns": {f"2024-01-{d:02d}": d / 100.0 for d in range(1, 41)}, "ic_ir": 0.1}
    assert reflect_sign({}) is None
    assert reflect_sign({"ic": 0.03}) is None
    assert reflect_sign({"ic": -0.03, **base}) is None
    assert reflect_sign({"ic": -0.05}) is None


def test_cull_factor_pool_structural_dedup_integration() -> None:
    from finaince.discovery import cull_factor_pool

    shared = {f"2024-01-{d:02d}": d / 100.0 for d in range(1, 13)}
    candidates = [
        {
            "role": "momentum",
            "hypothesis": "original",
            "expression": "Rank(Delta(close, 20))",
            "dialect": "repro_polars",
            "perf_metric": 0.04,
            "market_profile": "cn_stock",
            "returns": shared,
        },
        {
            "role": "momentum",
            "hypothesis": "near-duplicate",
            "expression": "rank(rank( delta(close,20) ))",
            "dialect": "repro_polars",
            "perf_metric": 0.039,
            "market_profile": "cn_stock",
            "returns": shared,
        },
    ]
    kept = cull_factor_pool(candidates)
    names = {item.get("hypothesis", "") for item in kept}
    assert not any("near-duplicate" in n for n in names)


def test_cull_factor_pool_mirror_row_scored_alongside() -> None:
    from finaince.discovery import cull_factor_pool

    returns = {f"2024-{m:02d}-{d:02d}": (d % 7) / 100.0 for m in range(1, 3) for d in range(1, 29)}
    candidate = {
        "role": "reverse",
        "hypothesis": "negative-strong",
        "expression": "Rank(-Delta(close, 25))",
        "dialect": "repro_polars",
        "perf_metric": -0.06,
        "ic": -0.06,
        "ic_ir": -1.1,
        "turnover": 0.3,
        "sharpe": -1.5,
        "market_profile": "cn_stock",
        "returns": returns,
    }
    pool = cull_factor_pool([candidate])
    assert len(pool) >= 1, f"both orientations died in cull: {pool}"
    mirror_tagged = any("[sign-mirrored]" in str(item.get("hypothesis", "")) for item in pool)
    engine_oriented = any(item.get("signal_direction") == -1 for item in pool)
    assert mirror_tagged or engine_oriented, f"no surviving orientation: {pool}"

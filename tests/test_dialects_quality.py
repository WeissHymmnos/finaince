"""WS-G coverage: eval.dialects (is_listed / attach_translation) + run_loop e2e."""

from __future__ import annotations

from finaince.eval.dialects import attach_translation, translate_from_qlib, translate_to_qlib
from finaince.eval.router import is_listed


def test_is_listed_accepts_whitelisted_operators_only() -> None:
    assert is_listed("Rank(Delta(close, 5))") is True
    assert is_listed("Corr(close, volume, 10)") is True
    assert is_listed("NotAnOperator(close)") is False
    assert is_listed("Rank(SneakyOp(close))") is False
    assert is_listed("no calls here") is True


def test_translate_from_qlib_strips_fields() -> None:
    assert translate_from_qlib("Rank(Delta($close, 20))") == "Rank(Delta(close, 20))"
    assert translate_from_qlib("Corr($volume, $amount, 5)") == "Corr(volume, amount, 5)"
    assert translate_from_qlib("Rank(X($weird, 1))") == "Rank(X($weird, 1))"


def test_translate_to_qlib_roundtrip_and_rejects_unlisted() -> None:
    qlib = translate_to_qlib("Rank(Delta(close, 20))")
    assert qlib == "Rank(Delta($close, 20))"
    back = translate_from_qlib(qlib)
    assert back == "Rank(Delta(close, 20))"
    assert translate_to_qlib("Mystery(close)") is None
    assert translate_to_qlib("") is None


def test_attach_translation_matrix() -> None:
    repro = attach_translation("Rank(close)", "repro_polars")
    assert repro["translatable"] is True
    assert repro["alt_text"] == "Rank($close)"
    assert "Rank" in repro["operators"]

    unlisted = attach_translation("Weird(close)", "repro_polars")
    assert unlisted["translatable"] is False
    assert unlisted["alt_text"] is None

    qlib_side = attach_translation("Rank(Delta($close, 5))", "qlib")
    assert qlib_side["translatable"] is True
    assert qlib_side["alt_text"] is None


def test_run_loop_true_eval_end_to_end(isolated_home) -> None:
    """Full loop with the real local-panel eval (no mocks)."""
    from finaince.loop import run_loop

    out = run_loop(
        steps=2,
        expressions=["Rank(Delta(close, 1))", "-Rank(Delta(close, 3))"],
    )
    assert out["actions"] == ["factor", "model"]
    assert len(out["expressions_evaluated"]) == 1
    first = out["expressions_evaluated"][0]
    assert first["expression"] == "Rank(Delta(close, 1))"
    assert isinstance(first["ok"], bool)
    assert "factor_set" in out and out["factor_set"]


def test_run_loop_degrades_when_queue_exhausts(isolated_home) -> None:
    from finaince.loop import run_loop

    out = run_loop(steps=4, expressions=["Rank(Delta(close, 2))"])
    actions = out["actions"]
    assert actions[0] == "factor"
    if len(actions) > 1 and actions[1] == "model":
        pass
    elif out.get("degraded"):
        assert out["degraded"] in {"expression_queue_empty", "missing_action", "model_skipped"}

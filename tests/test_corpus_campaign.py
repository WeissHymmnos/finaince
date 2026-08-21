"""WS-K corpus campaign tests: classification, resume, honest no_factors."""

from __future__ import annotations

import pytest

from finaince import corpus_campaign as cc


@pytest.fixture()
def corpus(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (root / name).write_bytes(b"%PDF-1.4 fake")
    return root


def test_classify_outcome_matrix() -> None:
    assert cc.classify_outcome(None) == ("failed", [], "empty_result")
    state, ids, err = cc.classify_outcome({"status": "no_factors", "factors": []})
    assert (state, ids, err) == ("no_factors", [], None)
    state, ids, _ = cc.classify_outcome({"status": "ok", "factors": [{"catalog_id": "c1"}]})
    assert (state, ids) == ("done", ["c1"])
    state, ids, err = cc.classify_outcome({"status": "error", "error": "boom: detail"})
    assert state == "failed" and err.startswith("boom")


def test_run_campaign_resume_and_no_factors(monkeypatch, corpus, isolated_home) -> None:
    outcomes = {
        "a.pdf": {"status": "no_factors", "factors": []},
        "b.pdf": {"status": "ok", "factors": [{"catalog_id": "cat_1"}, {"id": "cat_2"}]},
    }

    def fake_process(pdf_path, *, backtest_kwargs=None):
        name = pdf_path.rsplit("/", 1)[-1]
        if name not in outcomes:
            return {"state": "failed", "catalog_ids": [], "error": "extract_failed: x"}
        raw = outcomes[name]
        state, ids, error = cc.classify_outcome(raw)
        return {"state": state, "catalog_ids": ids, "error": error}

    monkeypatch.setattr(cc, "process_one", fake_process)

    first = cc.run_campaign(corpus)
    assert first["processed"] == 3
    stats = first["stats"]
    assert stats["done"] == 1
    assert stats["no_factors"] == 1
    assert stats["failed"] == 1
    assert stats["factors_cataloged"] == 2

    second = cc.run_campaign(corpus)
    assert second["already_terminal"] == 2
    assert second["processed"] == 1

    manifest = cc.load_manifest()
    a_entry = next(e for k, e in manifest["entries"].items() if k.endswith("a.pdf"))
    assert a_entry["status"] == "no_factors"
    assert a_entry["attempts"] == 1


def test_reset_failed_requeues_only_failed(monkeypatch, corpus, isolated_home) -> None:
    cc.save_manifest(
        {
            "schema_version": 1,
            "entries": {
                "/x/a.pdf": {"status": "failed", "attempts": 1},
                "/x/b.pdf": {"status": "done", "attempts": 1},
                "/gone/c.pdf": {"status": "failed", "attempts": 2},
            },
        }
    )
    moved = cc.reset_failed(corpus)
    assert moved == 0
    moved_any = cc.reset_failed()
    assert moved_any == 2
    manifest = cc.load_manifest()
    statuses = {k.split("/")[-1]: v["status"] for k, v in manifest["entries"].items()}
    assert statuses["a.pdf"] == "pending"
    assert statuses["b.pdf"] == "done"


def test_missing_files_marked_skipped(isolated_home) -> None:
    cc.save_manifest(
        {
            "schema_version": 1,
            "entries": {
                "/nowhere/gone.pdf": {"status": "pending", "attempts": 0},
                "/tmp/kept.pdf": {"status": "done", "attempts": 1},
            },
        }
    )
    out = cc.run_campaign("/definitely/not/here")
    entries = cc.load_manifest()["entries"]
    assert entries["/nowhere/gone.pdf"]["status"] == "skipped_missing"
    assert out["discovered"] == 0


def test_stats_summary_counts_and_totals(isolated_home) -> None:
    cc.save_manifest(
        {
            "schema_version": 1,
            "entries": {
                "p1": {"status": "done", "catalog_ids": ["a", "b"]},
                "p2": {"status": "no_factors", "catalog_ids": []},
                "p3": {"status": "failed", "catalog_ids": []},
            },
        }
    )
    stats = cc.stats_summary()
    assert stats["done"] == 1 and stats["no_factors"] == 1 and stats["failed"] == 1
    assert stats["total_known"] == 3
    assert stats["factors_cataloged"] == 2

"""Promotion claims prevent concurrent decisions from repeating engine writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event

import pytest

from finaince.catalog.store import FactorCatalog
from finaince.domain.factor import FactorExpression, FactorLineage, FactorRecord
from finaince.review import desk


@pytest.fixture
def promotion(isolated_home, monkeypatch):
    catalog = FactorCatalog()
    now = datetime.now(UTC)
    record = FactorRecord(
        id="concurrent-factor", name="concurrent factor", status="review",
        expression=FactorExpression(dialect="repro_polars", text="Rank(close)"),
        lineage=FactorLineage(source="manual", source_ref="concurrent-factor"),
        created_at=now, updated_at=now,
    )
    catalog.upsert(record)
    promotion_id = catalog.add_promotion(record.id, "to_library", "pending", {})
    monkeypatch.setattr(desk, "evaluate_gates", lambda *args, **kwargs: {"passed": True})
    return catalog, record, promotion_id


def _decision(catalog, promotion_id):
    return next(event["decision"] for event in catalog.list_promotions(None) if event["id"] == promotion_id)


def test_concurrent_approvals_write_once_with_no_database_lock(promotion, monkeypatch):
    catalog, record, promotion_id = promotion
    verified = Barrier(2)
    writes = []

    def gates(*args, **kwargs):
        verified.wait(timeout=5)
        return {"passed": True}

    def write_library(candidate):
        assert _decision(catalog, promotion_id) == "approving"
        # The real library writer also writes the catalog through a fresh
        # connection. This must succeed while the persistent claim is held.
        FactorCatalog().upsert(candidate)
        writes.append(candidate.id)

    monkeypatch.setattr(desk, "evaluate_gates", gates)
    monkeypatch.setattr(desk, "_write_library", write_library)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(desk.approve, promotion_id, adversary=False) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(result["ok"] for result in results) == [False, True]
    assert writes == [record.id]
    assert _decision(catalog, promotion_id) == "approved"
    assert catalog.get(record.id).status == "ready"


def test_reject_during_approval_verification_wins_before_engine_write(promotion, monkeypatch):
    catalog, record, promotion_id = promotion
    verifying, resume = Event(), Event()

    def gates(*args, **kwargs):
        verifying.set()
        assert resume.wait(timeout=5)
        return {"passed": True}

    def unexpected_write(candidate):
        pytest.fail("a rejected promotion must not reach the engine")

    monkeypatch.setattr(desk, "evaluate_gates", gates)
    monkeypatch.setattr(desk, "_write_library", unexpected_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        approving = pool.submit(desk.approve, promotion_id, adversary=False)
        try:
            assert verifying.wait(timeout=5)
            assert desk.reject(promotion_id)["ok"] is True
        finally:
            resume.set()
        assert approving.result(timeout=10)["ok"] is False
    assert _decision(catalog, promotion_id) == "rejected"
    assert catalog.get(record.id).status == "candidate"


def test_reject_and_second_approve_cannot_interrupt_active_writer(promotion, monkeypatch):
    catalog, record, promotion_id = promotion
    writing, resume = Event(), Event()

    def write_library(candidate):
        writing.set()
        assert resume.wait(timeout=5)

    monkeypatch.setattr(desk, "_write_library", write_library)
    with ThreadPoolExecutor(max_workers=1) as pool:
        approving = pool.submit(desk.approve, promotion_id, adversary=False)
        try:
            assert writing.wait(timeout=5)
            assert desk.reject(promotion_id)["ok"] is False
            assert desk.approve(promotion_id, adversary=False)["ok"] is False
            assert _decision(catalog, promotion_id) == "approving"
        finally:
            resume.set()
        assert approving.result(timeout=10)["ok"] is True
    assert _decision(catalog, promotion_id) == "approved"
    assert catalog.get(record.id).status == "ready"


def test_failed_engine_write_releases_claim_for_retry(promotion, monkeypatch):
    catalog, record, promotion_id = promotion
    attempts = []

    def write_library(candidate):
        attempts.append(candidate.id)
        if len(attempts) == 1:
            raise RuntimeError("engine unavailable")

    monkeypatch.setattr(desk, "_write_library", write_library)
    with pytest.raises(RuntimeError, match="engine unavailable"):
        desk.approve(promotion_id, adversary=False)
    assert _decision(catalog, promotion_id) == "pending"
    assert catalog.get(record.id).status == "review"
    assert desk.approve(promotion_id, adversary=False)["ok"] is True
    assert len(attempts) == 2
    assert _decision(catalog, promotion_id) == "approved"


def test_failed_rejection_releases_claim_for_retry(promotion, monkeypatch):
    catalog, record, promotion_id = promotion
    original_upsert = FactorCatalog.upsert

    def failing_upsert(self, candidate):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(FactorCatalog, "upsert", failing_upsert)
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        desk.reject(promotion_id)
    assert _decision(catalog, promotion_id) == "pending"
    assert catalog.get(record.id).status == "review"
    monkeypatch.setattr(FactorCatalog, "upsert", original_upsert)
    assert desk.reject(promotion_id)["ok"] is True
    assert _decision(catalog, promotion_id) == "rejected"


@pytest.mark.parametrize("decision", ["approving", "rejecting"])
def test_interrupted_claim_is_not_automatically_replayed(promotion, monkeypatch, decision):
    catalog, record, promotion_id = promotion
    assert catalog.transition_promotion(promotion_id, decision, expected="pending")
    assert desk.approve(promotion_id, adversary=False)["ok"] is False
    assert desk.reject(promotion_id)["ok"] is False
    assert _decision(catalog, promotion_id) == decision
    assert catalog.get(record.id).status == "review"

"""Branch coverage for flows earlier suites only touched indirectly.

Covers: brain_track HTTP flows, code_evolution rewrite/gates-fail branches,
db.ensure_columns persistence semantics, and the desk auth middleware contract.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class _Resp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content: bytes = b"{}",
        text: str = "",
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = text or content.decode("utf-8", "replace")
        self._json = json_data if json_data is not None else {}

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeBrainClient:
    def __init__(
        self,
        post=None,
        get=None,
    ) -> None:
        self._post = post or (lambda path, json=None: _Resp())
        self._get = get or (lambda path: _Resp())
        self.closed = False

    def post(self, path: str, json: Any = None) -> _Resp:
        return self._post(path, json)

    def get(self, path: str) -> _Resp:
        return self._get(path)

    def close(self) -> None:
        self.closed = True


def test_submit_rejected_on_non_201(monkeypatch) -> None:
    import finaince.brain_track as bt

    monkeypatch.setattr(bt, "has_brain_credentials", lambda: True)
    client = _FakeBrainClient(
        post=lambda path, json=None: _Resp(status_code=403, text="forbidden detail"),
    )
    monkeypatch.setattr(bt, "_client", lambda: client)
    out = bt.submit_expression("Rank($close)")
    assert out["ok"] is False
    assert out["adjudication_level"] == "none"
    assert out["reason"] == "submit_rejected:403"
    assert "forbidden" in out["detail"]
    assert client.closed is True


def test_submit_reports_missing_location(monkeypatch) -> None:
    import finaince.brain_track as bt

    monkeypatch.setattr(bt, "has_brain_credentials", lambda: True)
    client = _FakeBrainClient(post=lambda path, json=None: _Resp(status_code=200))
    monkeypatch.setattr(bt, "_client", lambda: client)
    out = bt.submit_expression("Rank($close)")
    assert out["ok"] is False
    assert out["reason"] == "missing_location"


def test_submit_happy_path_fetches_grade(monkeypatch) -> None:
    import finaince.brain_track as bt

    monkeypatch.setattr(bt, "has_brain_credentials", lambda: True)

    def post(path: str, json: Any = None) -> _Resp:
        return _Resp(status_code=201, headers={"Location": "/simulations/sim123"})

    def get(path: str) -> _Resp:
        if "/alphas/" in path:
            body = {"grade": "GOOD", "is": {"sharpe": 1.2, "fitness": 0.7}, "status": "ACTIVE"}
            return _Resp(content=json.dumps(body).encode("utf-8"), json_data=body)
        return _Resp(content=b'{"progress": "COMPLETE"}', json_data={"progress": "COMPLETE"})

    client = _FakeBrainClient(post=post, get=get)
    monkeypatch.setattr(bt, "_client", lambda: client)
    out = bt.submit_expression("Rank($close)")
    assert out["ok"] is True, out
    assert out["adjudication_level"] == "platform"
    assert out["alpha_id"] == "sim123"
    assert out["grade"] == "GOOD"
    assert out["is_sharpe"] == 1.2
    assert client.closed is True


def test_submit_poll_failure_surfaces_status(monkeypatch) -> None:
    import finaince.brain_track as bt

    monkeypatch.setattr(bt, "has_brain_credentials", lambda: True)

    def post(path: str, json: Any = None) -> _Resp:
        return _Resp(status_code=201, headers={"Location": "/simulations/sim9"})

    client = _FakeBrainClient(
        post=post,
        get=lambda path: _Resp(status_code=500, text="boom"),
    )
    monkeypatch.setattr(bt, "_client", lambda: client)
    out = bt.submit_expression("Rank($close)")
    assert out["ok"] is False
    assert out["reason"] == "simulation_poll_failed:500"


def test_submit_timeout_without_polling(monkeypatch) -> None:
    import finaince.brain_track as bt

    monkeypatch.setattr(bt, "has_brain_credentials", lambda: True)
    polls: list[str] = []

    def post(path: str, json: Any = None) -> _Resp:
        return _Resp(status_code=201, headers={"Location": "/simulations/x"})

    def get(path: str) -> _Resp:
        polls.append(path)
        return _Resp(content=b'{"progress": "0%"}', json_data={"progress": "0%"})

    client = _FakeBrainClient(post=post, get=get)
    monkeypatch.setattr(bt, "_client", lambda: client)
    out = bt.submit_expression("Rank($close)", max_seconds=0)
    assert out["ok"] is False
    assert out["reason"] == "simulation_timeout"
    assert polls == []


def test_evolve_rewrite_round_records_motive_and_recovers(monkeypatch, isolated_home) -> None:
    import finaince.code_evolution as ce
    import finaince.isolate as isolate_mod

    good_source = ce.default_seed("evo_rewrite_ok")
    real_run_isolated = isolate_mod.run_isolated

    def fake_run_isolated(source: str, **kwargs: Any) -> dict[str, Any]:
        if "open('x')" in source:
            return {"ok": False, "error": "NameError: name 'open' is not defined", "via": "frozen_builtin"}
        return real_run_isolated(source, **kwargs)

    monkeypatch.setattr(isolate_mod, "run_isolated", fake_run_isolated)
    monkeypatch.setattr(ce, "_chat_complete", lambda prompt: f"```python\n{good_source}\n```")
    result = ce.evolve_code_factor("rewrite recovery", seed_source=BAD_SOURCE, rounds=2)
    assert result["ok"] is True, result
    assert result["stage"] == "governed"
    assert result["rounds_used"] == 2
    assert result["drafts"][0]["ok"] is False
    assert result["drafts"][1]["edit_motive"]


def test_evolve_gates_fail_still_governed_without_promotion(monkeypatch, isolated_home) -> None:
    import finaince.code_evolution as ce
    import finaince.review.gates as gates_mod
    from finaince.catalog.store import FactorCatalog

    monkeypatch.setattr(
        gates_mod,
        "evaluate_gates",
        lambda record, direction="to_pool": {
            "passed": False,
            "failures": ["weak_ic"],
            "direction": direction,
            "details": {},
        },
    )
    result = ce.evolve_code_factor(
        "gates fail on purpose",
        seed_source=ce.default_seed("evo_gatefail"),
        rounds=1,
    )
    assert result["ok"] is True
    assert result["stage"] == "governed"
    assert result["gates"]["passed"] is False
    assert "weak_ic" in result["gates"]["failures"]
    row = FactorCatalog().get(result["catalog_id"])
    assert row is not None
    assert "pool" not in (row.tags or [])


def test_ensure_columns_adds_persists_and_is_idempotent(tmp_path) -> None:
    from finaince.db import ensure_columns

    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
    conn.close()

    conn = sqlite3.connect(db)
    ensure_columns(conn, "t", [("a", "TEXT"), ("b", "INTEGER")])
    cols_now = {r[1] for r in conn.execute("PRAGMA table_info(t)")}
    conn.close()
    assert cols_now == {"id", "a", "b"}

    fresh = sqlite3.connect(db)
    cols_persisted = {r[1] for r in fresh.execute("PRAGMA table_info(t)")}
    fresh.close()
    assert {"a", "b"} <= cols_persisted

    again = sqlite3.connect(db)
    ensure_columns(again, "t", [("a", "TEXT"), ("c", "TEXT")])
    cols_after = {r[1] for r in again.execute("PRAGMA table_info(t)")}
    again.close()
    assert cols_after == {"id", "a", "b", "c"}


def test_desk_gate_default_deny_public_allowlist_and_preflight(isolated_home) -> None:
    from fastapi.testclient import TestClient

    from finaince.serve import create_app

    client = TestClient(create_app())
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/no-such-endpoint").status_code == 401
    assert client.get("/api/v1/health").status_code == 200
    preflight = client.options(
        "/api/v1/jobs",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code != 401


BAD_SOURCE = """\
NAME = 'evo_bad'
EXPRESSION = ''
def compute(panel):
    close = panel['close']
    return [open('x') for _ in close]
"""


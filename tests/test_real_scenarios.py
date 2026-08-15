"""Real-entry scenarios: subprocess CLI + shipped pipeline + HTTP, no reimplementation."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

FINAINCE_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = FINAINCE_ROOT.parent
MINIMAL_PDF = DOCS_ROOT / "reproagent" / "tests" / "fixtures" / "sample_reports" / "minimal.pdf"
LOCAL_DATA = DOCS_ROOT / "reproagent" / "tests" / "fixtures" / "test_data"
PY = sys.executable


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["FINAINCE_HOME"] = str(home)
    env["LOCAL_DATA_PATH"] = str(LOCAL_DATA)
    env["APP_ENV"] = "dev"
    env["ALLOW_MOCK_LLM"] = "true"
    env["PYTHONPATH"] = str(FINAINCE_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(home: Path, args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "-m", "finaince", *args],
        cwd=str(FINAINCE_ROOT),
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _json_line(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise AssertionError(f"no JSON object in output:\n{text}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.skipif(not MINIMAL_PDF.is_file(), reason="minimal.pdf fixture missing")
def test_real_cli_reproduce_catalog_eval_promote(tmp_path: Path) -> None:
    """Subprocess the real CLI: reproduce PDF → catalog → eval → promote → fail-closed approve."""
    home = tmp_path / "fa-home"
    home.mkdir()
    pdf = MINIMAL_PDF
    assert pdf.is_file()

    doctor = _run(home, ["doctor"])
    assert doctor.returncode == 0, doctor.stderr + doctor.stdout
    doctor_body = json.loads(doctor.stdout)
    assert doctor_body["ok"] is True
    assert doctor_body["product_name"] == "FinAlpha"
    assert Path(doctor_body["home"]) == home

    validate = _run(home, ["validate", "Rank(Delta(close, 1))"])
    assert validate.returncode == 0, validate.stderr + validate.stdout
    validate_body = json.loads(validate.stdout)
    assert validate_body.get("valid") is True

    demo = _run(home, ["discover", "--demo"])
    assert demo.returncode == 0, demo.stderr + demo.stdout
    demo_body = _json_line(demo.stdout)
    assert demo_body["action"] == "discover"
    assert demo_body["kept_count"] >= 1
    assert any(row["decision"] == "cull" for row in demo_body["pool"])

    repro = _run(home, ["reproduce", str(pdf), "--sync"], timeout=180)
    assert repro.returncode == 0, repro.stderr + repro.stdout
    combined = repro.stdout + repro.stderr
    assert "reproduce ok" in combined or '"status"' in combined
    body = _json_line(repro.stdout)
    result = body.get("result") or body
    status = result.get("status") or (result.get("summary") or {}).get("status")
    assert status, body

    jobs = _run(home, ["jobs"])
    assert jobs.returncode == 0, jobs.stderr
    jobs_body = _json_line(jobs.stdout)
    assert jobs_body["count"] >= 1
    assert any(item.get("kind") == "reproduce_report" for item in jobs_body["items"])
    assert any(item.get("status") == "done" for item in jobs_body["items"])

    ev = _run(home, ["eval", "Rank(Delta(close, 1))", "--dialect", "repro_polars"])
    assert ev.returncode == 0, ev.stderr + ev.stdout
    ev_body = _json_line(ev.stdout)
    assert ev_body["ok"] is True
    metrics = ev_body["metrics"]
    assert any(isinstance(metrics.get(k), (int, float)) for k in ("ic_mean", "sharpe_ratio", "max_drawdown"))

    cat = _run(home, ["catalog"])
    assert cat.returncode == 0, cat.stderr
    cat_body = _json_line(cat.stdout)
    assert "items" in cat_body

    lib = _run(home, ["library"])
    assert lib.returncode == 0, lib.stderr
    lib_body = _json_line(lib.stdout)
    assert "count" in lib_body
    # Isolated FINAINCE_HOME must not leak ~/.reproagent (thousands of rows).
    assert lib_body["count"] < 50, lib.stdout
    if result.get("status") == "passed" or (result.get("summary") or {}).get("passed"):
        assert any(item.get("name") == "mock_momentum" for item in lib_body["items"])

    if result.get("status") == "passed" or (result.get("summary") or {}).get("passed"):
        assert cat_body["count"] >= 1, cat.stdout
        rec = cat_body["items"][0]
        assert rec["lineage"]["source"] == "reproduction"
        promo = _run(home, ["promote", rec["id"], "--to", "to_pool"])
        assert promo.returncode == 0, promo.stderr
        promo_body = _json_line(promo.stdout)
        assert promo_body.get("status") == "review"
        assert promo_body.get("promotion_id")
        gates = promo_body.get("gates") or {}
        assert "missing_returns" in (gates.get("failures") or []) or gates.get("passed") in {True, False}

        pending = _run(home, ["review"])
        assert pending.returncode == 0, pending.stderr
        pending_body = _json_line(pending.stdout)
        assert any(item["id"] == promo_body["promotion_id"] for item in pending_body["items"])

        approved = _run(home, ["review", "--approve", promo_body["promotion_id"]])
        assert approved.returncode == 0, approved.stderr
        approved_body = _json_line(approved.stdout)
        # Live fixture equity can be 0-row; approve must stay fail-closed.
        if not rec.get("daily_returns"):
            assert approved_body.get("ok") is False
            assert approved_body.get("error") in {"gates_failed", "empty_returns"}
            assert "missing_returns" in ((approved_body.get("gates") or {}).get("failures") or [])


@pytest.mark.skipif(not MINIMAL_PDF.is_file(), reason="minimal.pdf fixture missing")
def test_real_http_desk_against_cli_home(tmp_path: Path) -> None:
    """Same isolated home: CLI reproduce, then live uvicorn + httpx (not TestClient)."""
    httpx = pytest.importorskip("httpx")

    home = tmp_path / "fa-http"
    home.mkdir()
    repro = _run(home, ["reproduce", str(MINIMAL_PDF), "--sync"], timeout=180)
    assert repro.returncode == 0, repro.stderr + repro.stdout

    port = _free_port()
    proc = subprocess.Popen(
        [PY, "-m", "finaince", "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(FINAINCE_ROOT),
        env=_env(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 20
        last_exc: Exception | None = None
        while time.time() < deadline:
            try:
                health = httpx.get(f"{base}/api/v1/health", timeout=1.0)
                if health.status_code == 200:
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            time.sleep(0.2)
        else:
            raise AssertionError(f"serve never became ready: {last_exc}\n{proc.stderr.read() if proc.stderr else ''}")

        health = httpx.get(f"{base}/api/v1/health", timeout=5.0)
        assert health.status_code == 200
        assert health.json()["product"] == "FinAlpha"

        catalog = httpx.get(f"{base}/api/v1/catalog", timeout=5.0)
        assert catalog.status_code == 200
        cat_body = catalog.json()
        assert cat_body["count"] >= 1
        assert any(item["lineage"]["source"] == "reproduction" for item in cat_body["items"])

        ev = httpx.post(
            f"{base}/api/v1/eval",
            json={"expression": "Rank(Delta(close, 1))", "dialect": "repro_polars"},
            timeout=30.0,
        )
        assert ev.status_code == 200
        ev_body = ev.json()
        assert ev_body["ok"] is True
        assert ev_body["metrics"]

        jobs = httpx.get(f"{base}/api/v1/jobs", timeout=5.0)
        assert jobs.status_code == 200
        assert jobs.json()["count"] >= 1

        rec = cat_body["items"][0]
        promo = _run(home, ["promote", rec["id"], "--to", "to_pool"])
        promo_body = _json_line(promo.stdout)
        pid = promo_body["promotion_id"]

        queue = httpx.get(f"{base}/api/v1/review", timeout=5.0)
        assert queue.status_code == 200
        assert any(item["id"] == pid for item in queue.json()["items"])

        approve = httpx.post(f"{base}/api/v1/review/{pid}/approve", timeout=10.0)
        assert approve.status_code == 200
        denied = approve.json()
        if not rec.get("daily_returns"):
            assert denied.get("ok") is False
            assert denied.get("error") in {"gates_failed", "empty_returns"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_real_cli_sdk_info_has_desk_surface(tmp_path: Path) -> None:
    pytest.importorskip("claude_agent_sdk")
    home = tmp_path / "fa-home"
    home.mkdir()
    info = _run(home, ["sdk-info"])
    assert info.returncode == 0, info.stderr
    body = json.loads(info.stdout)
    assert body["product"] == "FinAlpha"
    assert body["has_system_prompt"] is True
    names = set(body["custom_tool_names"])
    assert {"catalog_list", "eval_expression", "reproduce_report", "promote_factor"} <= names
    assert "discover" in body["specialists"]
    hooks = {h["event"] for h in body["hooks"]}
    assert "PreToolUse" in hooks and "PostToolUse" in hooks


@pytest.mark.live
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
@pytest.mark.skipif(not os.environ.get("FINAINCE_LIVE_AGENT"), reason="set FINAINCE_LIVE_AGENT=1 for live agent")
def test_real_live_agent_reads_catalog(tmp_path: Path) -> None:
    """Live Claude Agent SDK: agent must call catalog tools, not invent the desk state."""
    if not MINIMAL_PDF.is_file():
        pytest.skip("minimal.pdf fixture missing")
    home = tmp_path / "fa-agent"
    home.mkdir()
    repro = _run(home, ["reproduce", str(MINIMAL_PDF), "--sync"], timeout=180)
    assert repro.returncode == 0, repro.stderr + repro.stdout
    cat = _json_line(_run(home, ["catalog"]).stdout)
    assert cat["count"] >= 1

    agent = _run(
        home,
        [
            "agent",
            "先 doctor，再 catalog_list。只根据工具结果回答：目录里有几条、来源、IC、daily_returns 是否为空。不要 review_approve，不要 discover_swarm。",
            "--max-turns",
            "8",
        ],
        timeout=180,
    )
    assert agent.returncode == 0, agent.stderr + agent.stdout
    body = json.loads(agent.stdout)
    assert body.get("ok") is True, body
    text = (body.get("text") or "").lower()
    assert "swarm" not in text or "未" in (body.get("text") or "") or "not" in text
    assert any(token in text for token in ("ic", "catalog", "reproduction", "因子", "目录"))

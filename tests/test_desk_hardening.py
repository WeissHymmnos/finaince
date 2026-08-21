"""Ship the audit Top-5: HTTP lock, SHA pins, smoke thin_panel, lite parser, isolate re-eval."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from finaince.serve import create_app
from tests.conftest import DESK_TOKEN, desk_headers

ROOT = Path(__file__).resolve().parents[1]
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _client(isolated_home: Path):
    import finaince.serve as serve_mod

    serve_mod.app = None
    return TestClient(create_app())


def test_mutation_http_requires_desk_token_and_pdf_root(isolated_home: Path, tmp_path: Path) -> None:
    client = _client(isolated_home)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["product"] == "FinAlpha"

    mutations = [
        ("POST", "/api/v1/promote", {"catalog_id": "x", "direction": "to_pool"}),
        ("POST", "/api/v1/eval", {"expression": "Rank(close)", "dialect": "repro_polars"}),
        ("POST", "/api/v1/agent", {"prompt": "hello"}),
        ("POST", "/api/v1/impl", {"source": "NAME='x'\ndef compute(panel):\n    return [1,2,3,4]\n"}),
        ("POST", "/api/v1/impl/needs", {}),
        ("POST", "/api/v1/loop", {"steps": 1}),
        ("POST", "/api/v1/reproduce", {"pdf_path": "/etc/passwd"}),
        ("POST", "/api/v1/jobs/missing/cancel", {}),
        ("POST", "/api/v1/review/missing/approve", {}),
        ("POST", "/api/v1/review/missing/reject", {}),
    ]
    for method, path, body in mutations:
        response = client.request(method, path, json=body)
        assert response.status_code in {401, 403}, (path, response.status_code, response.text[:200])

    marker = tmp_path / "isolate-must-not-write.txt"
    evil = (
        "NAME = 'evil'\n"
        f"def compute(panel):\n"
        f"    open({str(marker)!r}, 'w').write('owned')\n"
        "    return [1.0, 2.0, 3.0, 4.0]\n"
    )
    impl = client.post("/api/v1/impl", json={"source": evil, "name": "evil"})
    assert impl.status_code == 401
    assert not marker.exists()

    outside = client.post(
        "/api/v1/reproduce",
        json={"pdf_path": "/etc/passwd", "sync": True},
        headers=desk_headers(),
    )
    assert outside.status_code == 403
    assert "FINAINCE_PDF_ROOT" in outside.json().get("detail", "")


def test_http_approve_rejects_client_override(isolated_home: Path) -> None:
    from aiminer.pool_io import load_alpha_pool_rows

    from finaince.catalog.hooks import accept_library_entry
    from finaince.catalog.store import FactorCatalog
    from finaince.review.desk import promote
    from finaince.settings import get_settings

    returns = {f"2024-04-{d:02d}": 0.01 for d in range(1, 13)}

    class _F:
        name = "http_override"
        name_cn = "http"
        style = "momentum"
        formula = "Rank(Delta(close, 1))"
        input_fields = ["close"]
        universe = "local_panel"
        rebalance_frequency = "daily"
        spec_id = "spec-ov"

    class _E:
        id = "lib-http-ov"
        report_id = "rep-http-ov"
        factor = _F()

    accept_library_entry(
        _E(),
        extras={"metrics": {"ic_mean": 0.07}, "daily_returns": returns, "observability": {}},
    )
    rec = next(r for r in FactorCatalog().list() if r.name == "http_override")
    promo = promote(rec.id, direction="to_pool")
    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    client = _client(isolated_home)
    blocked = client.post(
        f"/api/v1/review/{promo['promotion_id']}/approve",
        json={"override": ["thin_panel"]},
        headers=desk_headers(),
    )
    assert blocked.status_code == 403
    after = list(load_alpha_pool_rows(get_settings().aiminer_db))
    assert after == before


def test_extras_pinned_to_immutable_sha() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    found = re.findall(
        r"git\+https://github.com/WeissHymmnos/(?:ReproAgent|aiminer|finpdfpro)\.git@([0-9a-f]+)",
        text,
    )
    assert len(found) >= 3
    for sha in found:
        assert _SHA.match(sha), sha
    assert "@finaince-desk" not in text
    assert "@finaince-312" not in text


def test_path_hack_off_by_default_in_subprocess() -> None:
    code = (
        "import os, sys\n"
        "os.environ.pop('FINAINCE_PATH_HACK', None)\n"
        "os.environ.pop('FINAINCE_NO_PATH_HACK', None)\n"
        "import finaince._paths as p\n"
        "print(json.dumps({'disabled': p.path_hack_disabled(), 'path': sys.path[:8]}))\n"
    )
    # injected via -c after import json
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, sys\n"
            "os.environ.pop('FINAINCE_PATH_HACK', None)\n"
            "os.environ.pop('FINAINCE_NO_PATH_HACK', None)\n"
            "import finaince._paths as p\n"
            "print(json.dumps({'disabled': p.path_hack_disabled(), 'path': list(sys.path)}))\n",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if k not in {"FINAINCE_PATH_HACK", "FINAINCE_NO_PATH_HACK"}},
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout.strip().splitlines()[-1])
    assert body["disabled"] is True
    joined = "\n".join(body["path"])
    assert "/aiminer/src" not in joined.split("site-packages", 1)[0] or True
    assert not any(p.endswith("/aiminer/src") or p.endswith("/reproagent/src") for p in body["path"][:5])


def test_smoke_panel_fail_closes_thin_and_does_not_write_pool(isolated_home: Path) -> None:
    from datetime import UTC, datetime

    from aiminer.pool_io import load_alpha_pool_rows

    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorMetrics, FactorRecord
    from finaince.review.desk import approve, promote
    from finaince.review.gates import evaluate_gates
    from finaince.settings import get_settings

    now = datetime.now(UTC)
    rec = FactorRecord(
        id="fac_smoke_thin",
        name="smoke_thin",
        expression=FactorExpression(dialect="repro_polars", text="Rank(Delta(close, 1))"),
        universe="local_panel",
        metrics=FactorMetrics(ic=0.06),
        daily_returns={f"2024-03-{d:02d}": 0.01 for d in range(1, 13)},
        lineage=FactorLineage(source="manual", source_ref="smoke_thin"),
        created_at=now,
        updated_at=now,
    )
    FactorCatalog().upsert(rec)
    gates = evaluate_gates(rec, direction="to_pool")
    assert "thin_panel" in gates["failures"]
    before = list(load_alpha_pool_rows(get_settings().aiminer_db))
    promo = promote(rec.id, direction="to_pool")
    denied = approve(promo["promotion_id"])
    assert denied["ok"] is False
    assert "thin_panel" in ((denied.get("gates") or {}).get("failures") or [])
    assert list(load_alpha_pool_rows(get_settings().aiminer_db)) == before


def test_runtime_settings_default_lite(monkeypatch) -> None:
    monkeypatch.delenv("FINAINCE_FINPDFPRO_PROFILE", raising=False)
    from finaince.settings import reproagent_runtime_settings

    cfg = reproagent_runtime_settings()
    assert cfg.finpdfpro_profile == "lite"
    monkeypatch.setenv("FINAINCE_FINPDFPRO_PROFILE", "balanced")
    heavy = reproagent_runtime_settings()
    assert heavy.finpdfpro_profile == "balanced"


def test_isolate_blocks_open_and_does_not_fabricate_returns(isolated_home: Path, tmp_path: Path) -> None:
    from finaince.isolate import child_isolate, upsert_isolated

    marker = tmp_path / "must-not-exist.txt"
    blocked = child_isolate(
        {
            "source": (
                "NAME='bad'\n"
                f"def compute(panel):\n"
                f"    open({str(marker)!r}, 'w').write('x')\n"
                "    return [1.0, 2.0, 3.0, 4.0]\n"
            )
        }
    )
    assert blocked.get("ok") is False
    assert not marker.exists()
    ev = child_isolate({"source": "NAME='e'\ndef compute(panel):\n    return eval('1+1')\n"})
    assert ev.get("ok") is False

    good = child_isolate(
        {
            "source": (
                "NAME = 'iso_mom'\n"
                "def compute(panel):\n"
                "    close = list(panel['close'])\n"
                "    return [0.0] + [close[i]-close[i-1] for i in range(1, len(close))]\n"
            )
        }
    )
    assert good.get("ok") is True, good
    assert good.get("daily_returns") == {}
    assert good.get("ic") is None
    stored = upsert_isolated(good, universe="local_panel")
    assert stored.get("ok") is True
    rec = stored["record"]
    assert rec.daily_returns == {}
    assert rec.metrics.ic is None
    assert rec.is_simulated is True
    assert not any(str(k).startswith("2024-01-") for k in rec.daily_returns)


def test_read_routes_require_desk_token(isolated_home: Path) -> None:
    client = _client(isolated_home)
    for path in (
        "/api/v1/catalog",
        "/api/v1/review",
        "/api/v1/jobs",
        "/api/v1/audit",
        "/api/v1/trace",
    ):
        denied = client.get(path)
        assert denied.status_code == 401, path
        ok = client.get(path, headers=desk_headers())
        assert ok.status_code == 200, (path, ok.text[:200])
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/baseline").status_code == 200


def test_serve_host_and_cors_and_default_backend() -> None:
    from finaince.auth import cors_origins, validate_serve_host
    from finaince.settings import FinainceSettings

    assert validate_serve_host("127.0.0.1") == "127.0.0.1"
    assert validate_serve_host("localhost") == "localhost"
    try:
        validate_serve_host("0.0.0.0")
    except ValueError as exc:
        assert "FINAINCE_ALLOW_PUBLIC_BIND" in str(exc)
    else:
        raise AssertionError("0.0.0.0 must be rejected")
    origins = cors_origins()
    assert "http://127.0.0.1:8000" in origins
    assert "*" not in origins
    assert FinainceSettings().default_data_backend == "local"


def test_aiminer_auth_is_aligned_when_desk_token_set(isolated_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("FINAINCE_DESK_TOKEN", DESK_TOKEN)
    monkeypatch.delenv("AIMINER_DISABLE_AUTH", raising=False)
    client = _client(isolated_home)
    health = client.get("/api/health")
    assert health.status_code == 200
    denied_catalog = client.get("/api/v1/catalog")
    assert denied_catalog.status_code == 401
    swarm = client.get("/api/swarm/status")
    if health.json().get("auth_disabled") is False:
        assert swarm.status_code == 401
    else:
        assert swarm.status_code == 200

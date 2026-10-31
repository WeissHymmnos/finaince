"""Offline regression tests for installed discovery and reproduction contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from finaince import corpus_campaign as cc


def test_swarm_fallback_without_sibling_paths(monkeypatch, isolated_home):
    from finaince.discovery import run_swarm

    monkeypatch.setenv("FINAINCE_NO_PATH_HACK", "1")
    monkeypatch.setenv("PYTHONPATH", "existing-import-path")
    monkeypatch.setitem(sys.modules, "aiminer.sub_agent", None)
    monkeypatch.setattr("finaince.runtime.aiminer_python", lambda: sys.executable)
    monkeypatch.setattr("finaince.settings.swarm_argv", lambda args: args)
    cfg = SimpleNamespace(
        home=isolated_home,
        aiminer_results=isolated_home / "results",
        apply_engine_env=lambda: None,
    )
    monkeypatch.setattr("finaince.settings.get_settings", lambda: cfg)
    monkeypatch.setattr("finaince.runtime.resolve_llm", lambda: {"api_key": "dummy-test-key"})
    monkeypatch.setattr(
        "finaince.runtime.inject_llm_env", lambda llm: monkeypatch.setenv("LLM_API_KEY", llm["api_key"])
    )
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs, argv=argv)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_swarm(["--iterations", "1"], timeout=12)
    assert out["ok"] is True
    assert captured["argv"] == [sys.executable, "-m", "aiminer.manager", "--iterations", "1"]
    assert captured["env"]["PYTHONPATH"] == "existing-import-path"
    assert captured["env"]["LLM_API_KEY"] == "dummy-test-key"
    assert captured["timeout"] == 12


def test_swarm_equals_options_keep_user_values(monkeypatch):
    from finaince.settings import swarm_argv

    monkeypatch.setattr("finaince.settings.resolve_llm", lambda: {
        "via": "gateway", "aiminer_provider": "openai", "model": "default-model", "base_url": "http://default/v1"
    })
    options = {
        "--llm-provider": "custom", "--llm-model": "custom-model", "--llm-base-url": "http://custom/v1",
        "--mode": "custom-mode", "--data-backend": "local", "--local-data-path": "/tmp/data",
        "--embedding-provider": "local",
    }
    args = [f"{flag}={value}" for flag, value in options.items()]
    assert swarm_argv(args) == args


@pytest.mark.parametrize("raw", [None, {}, [], "invalid"])
def test_empty_engine_output_is_failure(monkeypatch, offline_settings, raw):
    from finaince.reproduction import reproduce_report

    monkeypatch.setattr("reproagent.pipeline.reproduce_report", lambda *args, **kwargs: raw)
    with pytest.raises(RuntimeError, match="empty or invalid"):
        reproduce_report("unused.pdf", offline_settings)


def test_reproduction_resolves_engine_id_to_catalog_id(monkeypatch, offline_settings, isolated_home):
    from finaince.catalog.store import FactorCatalog
    from finaince.domain.factor import FactorExpression, FactorLineage, FactorRecord
    from finaince.reproduction import reproduce_report

    catalog = FactorCatalog()
    record = FactorRecord(
        id="catalog-record", name="factor", expression=FactorExpression(dialect="repro_polars", text="close"),
        lineage=FactorLineage(source="reproduction", source_ref="engine-entry"),
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    catalog.upsert(record)
    assert catalog.get_by_source_ref("discovery", "engine-entry") is None
    monkeypatch.setattr("reproagent.pipeline.reproduce_report", lambda *args, **kwargs: {
        "status": "passed", "factors": [{"factor_id": "engine-entry", "status": "passed"}],
    })
    result = reproduce_report("unused.pdf", offline_settings)
    applied = cc.apply_report_outcome(result)
    assert applied["state"] == "done"
    assert applied["catalog_ids"] == ["catalog-record"]
    assert "catalog_id" not in result["factors"][0]


@pytest.mark.parametrize("raw", [
    {}, [], {"status": "error"}, {"status": "failed"}, {"status": "cancelled"},
    {"status": "ok", "error": "parse failed", "factors": []},
    {"status": "no_factors", "error": "parse failed", "factors": []},
    {"status": "passed", "factors": [{"factor_id": "uncataloged"}]},
    {"status": "ok", "factors": ["invalid-factor"]},
    {"status": "ok", "factors": {}},
    {"status": "ok", "factors": ""},
])
def test_campaign_does_not_mark_failed_reports_terminal(raw):
    state, ids, error = cc.classify_outcome(raw)
    assert state == "failed"
    assert ids == []
    assert error


def test_campaign_catalog_failure_is_recorded_per_report(monkeypatch):
    monkeypatch.setattr("finaince.reproduction.reproduce_report", lambda *args, **kwargs: {
        "status": "passed", "factors": [{"factor_id": "engine-entry"}],
    })

    def failed_catalog(result):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(cc, "apply_report_outcome", failed_catalog)
    assert cc.process_one("unused.pdf") == {
        "state": "failed", "catalog_ids": [], "error": "catalog unavailable",
    }


@pytest.mark.parametrize("payload", [[], None, {"entries": {"a.pdf": None}}])
def test_campaign_recovers_invalid_manifest_shapes(payload, isolated_home):
    path = cc.campaign_dir() / "manifest.json"
    path.write_text(json.dumps(payload))
    assert cc.load_manifest()["entries"] == {}
    assert path.with_suffix(".corrupt").is_file()


def test_campaign_reset_respects_directory_boundary(tmp_path, isolated_home):
    root = tmp_path / "reports"
    inside, outside = str(root / "a.pdf"), str(tmp_path / "reports-backup" / "b.pdf")
    cc.save_manifest({"schema_version": 1, "entries": {
        inside: {"status": "failed"}, outside: {"status": "failed"},
    }})
    assert cc.reset_failed(root) == 1
    entries = cc.load_manifest()["entries"]
    assert entries[inside]["status"] == "pending"
    assert entries[outside]["status"] == "failed"


def test_doctor_panel_remains_structured(isolated_home, monkeypatch):
    from finaince.settings import doctor_report

    monkeypatch.setattr("finaince.eval.qlib_subprocess.qlib_subprocess_enabled", lambda: False)
    panel = doctor_report()["panel"]
    assert isinstance(panel, dict)
    assert "n_assets" in panel and "thin" in panel


def test_packaged_frontend_escapes_all_rendered_api_fields():
    html = Path(__file__).resolve().parents[1] / "src" / "finaince" / "web" / "index.html"
    harness = r'''
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const script = fs.readFileSync(process.argv[1], "utf8").split("<script>")[1].split("</script>")[0];
const elements = [];
class Element {
  constructor() { this.value = ""; this.innerHTML = ""; this.textContent = ""; elements.push(this); }
  append() {}
  querySelector() { return new Element(); }
}
const ids = {};
const payload = '<img src=x onerror="globalThis.stolen=true">&';
const paths = [];
const rows = {
  "/api/v1/catalog": {items: [{id: "record", name: payload, status: payload, engine: payload,
      window_start: payload, window_end: payload, turnover_model: payload, formula: payload,
      metrics: {cost_bps: payload}}]},
  "/api/v1/review": {items: [{id: "promo/../part", catalog_id: payload, direction: payload, created_at: payload}]},
  "/api/v1/trace?limit=30": {items: [{action: payload, ts: payload, summary: payload}]},
  "/api/v1/jobs": {items: [{id: "job", status: payload, error: payload, trace_id: payload}]},
};
const context = vm.createContext({
  document: {getElementById: id => ids[id] ||= new Element(), createElement: () => new Element()},
  location: {search: ""}, URLSearchParams,
  fetch: async path => {
    paths.push(path);
    return {ok: true, json: async () => rows[path] || {promotion_id: payload, failures: [payload], details: {payload}}};
  },
});
vm.runInContext(script, context);
(async () => {
  await vm.runInContext('Promise.all([loadCatalog(), loadReview(), loadTrace(), loadJobs(), showGates("record", "promo/../part")])', context);
  const rendered = elements.map(el => el.innerHTML).filter(Boolean);
  assert(rendered.length >= 5);
  for (const html of rendered) {
    assert(!html.includes("<img"), html);
    assert(!html.includes(payload), html);
  }
  for (const id of ["catalog-list", "review-list", "trace-list", "job-list"]) {
    assert.strictEqual(ids[id].textContent, "", id);
  }
  assert(rendered.some(html => html.includes("&lt;img src=x onerror=&quot;")));
  assert(paths.includes("/api/v1/review/promo%2F..%2Fpart/gates"));
  assert(!paths.some(path => path.includes("/promo/../part/")));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run(
        ["node", "-e", harness, str(html)], capture_output=True, text=True, timeout=15,
        env={**os.environ, "NODE_OPTIONS": ""},
    )
    assert completed.returncode == 0, completed.stderr

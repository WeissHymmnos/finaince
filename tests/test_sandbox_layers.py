"""WS-E sandbox layer tests: mode selection, bwrap argv shape, fallback honesty."""

from __future__ import annotations


def test_sandbox_mode_env(monkeypatch) -> None:
    from finaince.isolate import sandbox_mode

    monkeypatch.delenv("FINAINCE_SANDBOX", raising=False)
    assert sandbox_mode() == "auto"
    for value, expected in (("off", "off"), ("bwrap", "bwrap"), ("AUTO", "auto"), ("junk", "auto")):
        monkeypatch.setenv("FINAINCE_SANDBOX", value)
        assert sandbox_mode() == expected


def test_bwrap_command_shape(monkeypatch) -> None:
    from finaince.isolate import _bwrap_command

    argv = _bwrap_command()
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv
    assert "--new-session" in argv
    assert "--die-with-parent" in argv
    idx = argv.index("--")
    tail = argv[idx + 1 :]
    assert tail[-2:] == ["-m", "finaince.isolate"]
    ro_binds = [argv[i + 1] for i, token in enumerate(argv) if token == "--ro-bind"]
    assert len(ro_binds) >= 1
    for bind in ro_binds:
        assert ":" not in bind


def test_sandbox_backend_reports_active_layer(monkeypatch) -> None:
    from finaince.isolate import bwrap_available, sandbox_backend

    report = sandbox_backend()
    expected = "bwrap" if (sandbox_mode_default(monkeypatch) == "bwrap" or (sandbox_mode_default(monkeypatch) == "auto" and bwrap_available())) else "frozen_builtin"
    assert report["active"] == expected
    assert "frozen_builtin" in report["layers"]


def sandbox_mode_default(monkeypatch) -> str:
    from finaince.isolate import sandbox_mode

    return sandbox_mode()


def test_run_isolated_tags_via_and_off_mode_matches_baseline(monkeypatch) -> None:
    from finaince.isolate import child_isolate, run_isolated

    source = (
        "NAME='via_check'\n"
        "EXPRESSION='Rank(Delta(close, 3))'\n"
        "def compute(panel):\n"
        "    close = panel['close']\n"
        "    return [b - a for a, b in zip(close, close[1:])]\n"
    )
    baseline = child_isolate({"source": source})

    monkeypatch.setenv("FINAINCE_SANDBOX", "off")
    result_off = run_isolated(source)
    assert result_off.get("ok") is True
    assert result_off.get("values") == baseline.get("values")

    monkeypatch.setenv("FINAINCE_SANDBOX", "bwrap")
    result_forced = run_isolated(source)
    layer = result_forced.get("via")
    assert layer in {"bwrap", "frozen_builtin"}
    if layer == "frozen_builtin":
        assert result_forced.get("sandbox_fallback") is True
        assert result_forced.get("sandbox_fallback_reason")


def test_doctor_includes_sandbox_backend(isolated_home) -> None:
    from finaince.settings import doctor_report

    report = doctor_report()
    section = report["sandbox_backend"]
    assert section["mode"] in {"auto", "bwrap", "off"}
    assert section["active"] in {"bwrap", "frozen_builtin"}

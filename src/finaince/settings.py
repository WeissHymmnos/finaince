"""Unified FinainceSettings façade."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from finaince.runtime import (
    aiminer_python,
    default_backtest_window,
    has_rq_credentials,
    inject_llm_env,
    load_engine_dotenv,
    local_data_path,
    mock_llm_requested,
    pdf_root,
    resolve_data_source,
    resolve_deepseek_llm,
)


class FinainceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FINAINCE_", extra="ignore")

    product_name: str = "FinAlpha"
    home: Path = Field(default_factory=lambda: Path("~/.finaince").expanduser())
    catalog_enabled: bool = True
    auto_promote: bool = False
    default_data_backend: str = "ricequant"
    local_data_path: Path | None = None
    llm_provider: str | None = "deepseek"
    llm_api_key: str = ""
    llm_model: str | None = None
    llm_base_url: str | None = None
    pdf_root: Path | None = None
    rustminer_db: Path | None = None
    serve_spa: bool = True
    serve_host: str = "127.0.0.1"
    serve_port: int = 8000

    @property
    def platform_db(self) -> Path:
        return self.home / "platform.db"

    @property
    def aiminer_results(self) -> Path:
        return self.home / "aiminer" / "results"

    @property
    def aiminer_db(self) -> Path:
        return self.aiminer_results / "alpha_miner.db"

    @property
    def repro_data_dir(self) -> Path:
        return self.home / "reproagent"

    def apply_engine_env(self) -> None:
        """Export AIMINER_* / repro dirs / DeepSeek before importing engines."""
        load_engine_dotenv()
        self.home.mkdir(parents=True, exist_ok=True)
        self.aiminer_results.mkdir(parents=True, exist_ok=True)
        self.repro_data_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("AIMINER_DATA_DIR", str(self.home / "aiminer" / "data"))
        os.environ.setdefault("AIMINER_RESULTS_DIR", str(self.aiminer_results))
        data = self.local_data_path or local_data_path()
        if data:
            os.environ.setdefault("AIMINER_LOCAL_DATA_PATH", str(data))
            os.environ.setdefault("LOCAL_DATA_PATH", str(data))
        os.environ.setdefault("FINAINCE_HOME", str(self.home))
        if self.pdf_root:
            os.environ.setdefault("FINAINCE_PDF_ROOT", str(self.pdf_root))
        llm = resolve_deepseek_llm()
        key = self.llm_api_key or llm["api_key"]
        provider = self.llm_provider or llm["aiminer_provider"]
        if key:
            inject_llm_env({**llm, "api_key": key})
            if llm.get("via") != "cpa":
                inject_aiminer_api_key(provider, key)
        if llm.get("base_url"):
            os.environ.setdefault("AIMINER_LLM_BASE_URL", str(llm["base_url"]))
        try:
            from reproagent.settings import get_settings

            get_settings.cache_clear()
        except Exception:
            pass


def get_settings() -> FinainceSettings:
    load_engine_dotenv()
    cfg = FinainceSettings()
    if cfg.local_data_path is None:
        cfg.local_data_path = local_data_path()
    if cfg.pdf_root is None:
        cfg.pdf_root = pdf_root()
    return cfg


def sibling_fixture_data() -> Path | None:
    """Bundled reproagent local parquet used for offline backtests."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "reproagent" / "tests" / "fixtures" / "test_data"
        if (cand / "prices.parquet").is_file():
            return cand
    return None


def reproagent_runtime_settings():
    """Settings the CLI/jobs pass to the shipped reproduce_report."""
    from reproagent.settings import Settings, get_settings as repro_get

    cfg = get_settings()
    cfg.apply_engine_env()
    repro_get.cache_clear()
    if mock_llm_requested():
        local = cfg.local_data_path or sibling_fixture_data()
        return Settings(
            _env_file=None,
            app_env="dev",
            allow_mock_llm=True,
            allow_formula_fallback=True,
            # Empty key wins over process/.env LLM_API_KEY so hermetic tests stay mock.
            llm_api_key="",
            llm_base_url=None,
            parser_backend="finpdfpro",
            finpdfpro_profile="balanced",
            finpdfpro_formula_backend="l1",
            data_source="local",
            local_data_path=local,
            data_dir=cfg.repro_data_dir,
            memory_enabled=True,
        )
    llm = resolve_deepseek_llm()
    data_source = resolve_data_source()
    local = cfg.local_data_path or local_data_path() or sibling_fixture_data()
    return Settings(
        _env_file=None,
        app_env="prod",
        allow_mock_llm=False,
        allow_formula_fallback=False,
        llm_provider=llm["repro_provider"],
        llm_api_key=llm["api_key"],
        llm_base_url=llm["base_url"],
        llm_model=llm["model"],
        parser_backend="finpdfpro",
        finpdfpro_profile="balanced",
        finpdfpro_formula_backend="l1",
        data_source=data_source,  # type: ignore[arg-type]
        local_data_path=local,
        data_dir=cfg.repro_data_dir,
        memory_enabled=True,
    )


def inject_aiminer_api_key(provider: str, key: str) -> None:
    mapping = {
        "openai": "OpenAI_KEY",
        "claude": "ClaudeCode_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "glm": "GLM_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env = mapping.get((provider or "").lower())
    if env:
        os.environ.setdefault(env, key)
    os.environ.setdefault("LLM_API_KEY", key)
    os.environ.setdefault("FINAINCE_LLM_API_KEY", key)


def default_reproduce_backtest_kwargs() -> dict[str, Any]:
    return default_backtest_window()


def swarm_argv(user_args: list[str] | None = None) -> list[str]:
    """Inject CPA DeepSeek + ricequant unless the caller already set them."""
    args = list(user_args or [])
    llm = resolve_deepseek_llm()

    def _has(flag: str) -> bool:
        return flag in args

    extras: list[str] = []
    if not _has("--llm-provider"):
        extras += ["--llm-provider", "deepseek"]
    if not _has("--llm-model"):
        extras += ["--llm-model", llm["model"]]
    if not _has("--llm-base-url") and llm.get("base_url"):
        extras += ["--llm-base-url", str(llm["base_url"])]
    if not _has("--mode"):
        extras += ["--mode", "ricequant"]
    if not _has("--data-backend"):
        extras += ["--data-backend", "ricequant" if has_rq_credentials() else "local"]
    if not _has("--local-data-path"):
        local = local_data_path()
        if local:
            extras += ["--local-data-path", str(local)]
    if not _has("--embedding-provider"):
        # DeepSeek/CPA is chat-only; wiki/RAG must not hit gptsapi with that key.
        extras += ["--embedding-provider", "local"]
    return extras + args


def doctor_report(settings: FinainceSettings | None = None, *, audit_check: bool = False) -> dict[str, Any]:
    cfg = settings or get_settings()
    issues: list[str] = []
    ok = True
    if not cfg.home.exists():
        try:
            cfg.home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ok = False
            issues.append(f"cannot create home: {exc}")
    provider = (cfg.llm_provider or "").lower()
    if provider and provider not in {
        "openai",
        "anthropic",
        "claude",
        "glm",
        "qwen",
        "kimi",
        "deepseek",
        "mimo",
        "",
    }:
        ok = False
        issues.append(f"unknown llm_provider={cfg.llm_provider!r}")
    llm = resolve_deepseek_llm(probe=False)
    data = cfg.local_data_path or local_data_path()
    root = cfg.pdf_root or pdf_root()
    pdf_sample = None
    if root.is_dir():
        pdf_sample = next(root.rglob("*.pdf"), None)
    elif not root.exists():
        issues.append(f"pdf_root missing: {root}")
    from finaince._paths import path_hack_disabled
    from finaince.runtime import local_panel_stats

    imports: dict[str, bool] = {}
    for name in ("finaince", "aiminer", "reproagent"):
        try:
            __import__(name)
            imports[name] = True
        except Exception:
            imports[name] = False
    aiminer_api = False
    try:
        from aiminer.api import app as _aiminer_app  # noqa: F401

        aiminer_api = True
    except Exception:
        aiminer_api = False
    qlib_extra = False
    try:
        import qlib  # noqa: F401

        qlib_extra = True
    except Exception:
        qlib_extra = False
    ws_lib = False
    try:
        import websockets  # noqa: F401

        ws_lib = True
    except Exception:
        ws_lib = False
        ok = False
        issues.append("websockets package missing; /ws upgrades return 404")
    memory_ok = False
    try:
        from reproagent.persistence.tables import ReportKnowledgeTable  # noqa: F401

        memory_ok = True
    except Exception:
        memory_ok = False
    panel = local_panel_stats()
    orphan = Path.cwd() / "results" / "alpha_miner.db"
    audit = None
    if audit_check:
        from finaince.catalog.audit import verify_tail

        audit = verify_tail()
    return {
        "product_name": cfg.product_name,
        "ok": ok,
        "home": str(cfg.home),
        "platform_db": str(cfg.platform_db),
        "aiminer_db": str(cfg.aiminer_db),
        "repro_data_dir": str(cfg.repro_data_dir),
        "catalog_enabled": cfg.catalog_enabled,
        "issues": issues,
        "mock_llm": mock_llm_requested(),
        "data_source": resolve_data_source(),
        "ricequant_creds": has_rq_credentials(),
        "local_data": str(data) if data else None,
        "local_data_exists": bool(data and Path(data).exists()),
        "pdf_root": str(root),
        "pdf_root_exists": root.is_dir(),
        "pdf_sample": str(pdf_sample) if pdf_sample else None,
        "llm": {
            "via": llm["via"],
            "model": llm["model"],
            "base_url": llm["base_url"],
            "has_key": bool(llm["api_key"]),
        },
        "aiminer_python": aiminer_python(),
        "os_name": os.name,
        "platform": __import__("sys").platform,
        "backtest_window": {
            k: v.isoformat() for k, v in default_backtest_window().items()
        },
        "parser": _parser_doctor(),
        "path_hack": not path_hack_disabled(),
        "imports": imports,
        "aiminer_api": aiminer_api,
        "qlib_extra": qlib_extra,
        "websockets": ws_lib,
        "memory_tables": memory_ok,
        "panel": panel,
        "extract_model": llm["model"],
        "orphan_results": orphan.is_file(),
        "audit": audit,
    }


def _parser_doctor() -> dict[str, Any]:
    try:
        from reproagent.parser.layout_extractor import _parser_identity, prefer_latest_finpdfpro

        prefer_latest_finpdfpro()
        ident = _parser_identity()
        ok = "finpdfpro" in ident.get("module", "") and not ident.get("version", "").startswith("0.2")
        return {**ident, "ok": ok}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

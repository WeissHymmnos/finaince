"""Resolve real PDF / market-data / DeepSeek (CPA) endpoints. No secrets logged."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_LOCAL_DATA = Path.home() / "Documents" / "Data"
CPA_HOST_DEFAULT = "http://127.0.0.1:8317"
# instructor tool_choice is rejected by DeepSeek V4 thinking models on CPA.
CPA_DEEPSEEK_MODEL = "deepseek-chat"
OFFICIAL_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
RQ_EVAL_START = date(2024, 1, 2)
RQ_EVAL_END = date(2024, 3, 29)


def documents_root() -> Path:
    from finaince._paths import documents_root as _root

    return _root()


def default_pdf_root() -> Path:
    root = documents_root()
    sibling = root / "KnowledgeBase" / "Quant" / "WH" / "Articles" / "categorized"
    if sibling.is_dir():
        return sibling
    home = Path.home() / "Documents" / "KnowledgeBase" / "Quant" / "WH" / "Articles" / "categorized"
    if home.is_dir():
        return home
    return sibling


DEFAULT_PDF_ROOT = default_pdf_root()


def load_engine_dotenv() -> None:
    """Load sibling .env files without overriding a live process env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = documents_root()
    for path in (root / "aiminer" / ".env", root / "reproagent" / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
    if not os.getenv("DEEPSEEK_API_KEY") and os.getenv("Deepseek_KEY"):
        os.environ["DEEPSEEK_API_KEY"] = os.environ["Deepseek_KEY"]


def mock_llm_requested() -> bool:
    raw = os.environ.get("ALLOW_MOCK_LLM", os.environ.get("FINAINCE_ALLOW_MOCK_LLM", ""))
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def has_rq_credentials() -> bool:
    token = (os.getenv("RQ_TOKEN") or "").strip()
    user = (os.getenv("RQ_USER") or "").strip()
    password = (os.getenv("RQ_PASS") or "").strip()
    return bool(token or (user and password))


def packaged_local_panel() -> Path | None:
    """In-tree fixture shipped with the wheel. Thin on purpose; not CSI300."""
    here = Path(__file__).resolve().parent / "data" / "local_panel"
    if (here / "prices.parquet").is_file():
        return here
    return None


def local_data_path() -> Path | None:
    for key in ("FINAINCE_LOCAL_DATA_PATH", "LOCAL_DATA_PATH"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    if (DEFAULT_LOCAL_DATA / "prices.parquet").is_file():
        return DEFAULT_LOCAL_DATA
    return packaged_local_panel()


def pdf_root() -> Path:
    raw = (os.getenv("FINAINCE_PDF_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return default_pdf_root()


def _openai_base(host: str) -> str:
    host = host.rstrip("/")
    if host.endswith("/v1"):
        return host
    return host + "/v1"


def cpa_base_url() -> str:
    return (os.getenv("ANTHROPIC_BASE_URL") or CPA_HOST_DEFAULT).rstrip("/")


def cpa_api_key() -> str:
    return (
        os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("CPA_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or ""
    ).strip()


def cpa_reachable(timeout: float = 1.5) -> bool:
    key = cpa_api_key()
    url = _openai_base(cpa_base_url()) + "/models"
    try:
        req = Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (URLError, OSError, TimeoutError, ValueError):
        return False


def official_deepseek_key() -> str:
    for key in ("DEEPSEEK_API_KEY", "Deepseek_KEY", "LLM_API_KEY"):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def resolve_deepseek_llm(*, probe: bool = False) -> dict[str, Any]:
    """Prefer CPA's DeepSeek route; fall back to official api.deepseek.com."""
    load_engine_dotenv()
    model = os.getenv("FINAINCE_LLM_MODEL") or CPA_DEEPSEEK_MODEL
    cpa_key = cpa_api_key()
    use_cpa = bool(cpa_key) and (cpa_reachable() if probe else bool(cpa_base_url()))
    if use_cpa:
        return {
            "via": "cpa",
            "aiminer_provider": "deepseek",
            "repro_provider": "openai",
            "api_key": cpa_key,
            "base_url": _openai_base(cpa_base_url()),
            "model": model,
        }
    official = official_deepseek_key()
    if official:
        return {
            "via": "deepseek-official",
            "aiminer_provider": "deepseek",
            "repro_provider": "openai",
            "api_key": official,
            "base_url": OFFICIAL_DEEPSEEK_BASE,
            "model": os.getenv("FINAINCE_LLM_MODEL") or "deepseek-v4-flash",
        }
    return {
        "via": "missing",
        "aiminer_provider": "deepseek",
        "repro_provider": "openai",
        "api_key": "",
        "base_url": _openai_base(cpa_base_url()),
        "model": model,
    }


def resolve_data_source() -> str:
    forced = (os.getenv("FINAINCE_DATA_SOURCE") or "").strip().lower()
    if forced in {"ricequant", "local"}:
        return forced
    if mock_llm_requested() and not os.getenv("FINAINCE_FORCE_REAL_DATA"):
        return "local"
    if has_rq_credentials():
        return "ricequant"
    return "local"


def local_panel_stats(path: Path | None = None) -> dict[str, Any]:
    target = path or local_data_path()
    if target is None:
        return {"n_assets": 0, "n_days": 0, "thin": True}
    parquet = target / "prices.parquet" if target.is_dir() else target
    if not parquet.is_file():
        return {"n_assets": 0, "n_days": 0, "thin": True}
    try:
        import polars as pl

        df = pl.read_parquet(parquet)
        code_col = "ts_code" if "ts_code" in df.columns else "instrument"
        date_col = "trade_date" if "trade_date" in df.columns else "datetime"
        n_assets = int(df[code_col].n_unique()) if code_col in df.columns else 0
        n_days = int(df[date_col].n_unique()) if date_col in df.columns else 0
        thin = n_assets < 50 or n_days < 60
        return {"n_assets": n_assets, "n_days": n_days, "thin": thin}
    except Exception:
        return {"n_assets": 0, "n_days": 0, "thin": True}


def local_panel_is_thin(path: Path | None = None) -> bool:
    return bool(local_panel_stats(path).get("thin"))


def default_universe(data_source: str | None = None) -> str:
    source = data_source or resolve_data_source()
    if source == "local" and local_panel_is_thin():
        return "local_panel"
    return "csi300"


def detect_local_date_range(path: Path | None) -> tuple[date, date] | None:
    if path is None:
        return None
    parquet = path / "prices.parquet" if path.is_dir() else path
    if not parquet.is_file():
        return None
    try:
        import polars as pl

        df = pl.read_parquet(parquet, columns=["trade_date"])
        lo, hi = df["trade_date"].min(), df["trade_date"].max()
        if lo is None or hi is None:
            return None
        if hasattr(lo, "date"):
            lo = lo.date()
        if hasattr(hi, "date"):
            hi = hi.date()
        return date.fromisoformat(str(lo)[:10]), date.fromisoformat(str(hi)[:10])
    except Exception:
        return None


def default_backtest_window(data_source: str | None = None) -> dict[str, date]:
    env_start = (os.getenv("FINAINCE_BT_START") or "").strip()
    env_end = (os.getenv("FINAINCE_BT_END") or "").strip()
    if env_start and env_end:
        return {
            "start_date": date.fromisoformat(env_start),
            "end_date": date.fromisoformat(env_end),
        }
    source = data_source or resolve_data_source()
    if source == "local":
        found = detect_local_date_range(local_data_path())
        if found:
            return {"start_date": found[0], "end_date": found[1]}
    return {"start_date": RQ_EVAL_START, "end_date": RQ_EVAL_END}


def aiminer_python() -> str:
    import sys

    from finaince.compat import conda_env_roots, python_in_env

    explicit = (os.getenv("AIMINER_PYTHON") or os.getenv("FINAINCE_AIMINER_PYTHON") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
    for root in conda_env_roots("aiminer"):
        found = python_in_env(root)
        if found is not None:
            return str(found)
    return sys.executable


def inject_llm_env(llm: dict[str, Any]) -> None:
    key = llm.get("api_key") or ""
    if not key:
        return
    # CPA's local token must win over official DEEPSEEK_API_KEY from .env,
    # otherwise aiminer hits 127.0.0.1:8317 with the cloud key and gets 401.
    if llm.get("via") == "cpa":
        os.environ["DEEPSEEK_API_KEY"] = key
        os.environ["LLM_API_KEY"] = key
        os.environ["FINAINCE_LLM_API_KEY"] = key
    else:
        os.environ.setdefault("DEEPSEEK_API_KEY", key)
        os.environ.setdefault("LLM_API_KEY", key)
        os.environ.setdefault("FINAINCE_LLM_API_KEY", key)
    if llm.get("base_url"):
        os.environ["FINAINCE_LLM_BASE_URL"] = str(llm["base_url"])

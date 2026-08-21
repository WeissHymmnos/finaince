"""Resolve PDF, market-data, and chat-LLM endpoints. No secrets logged."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_LOCAL_DATA = Path.home() / "Documents" / "Data"
OFFICIAL_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
OFFICIAL_OPENAI_BASE = "https://api.openai.com/v1"

_VENDOR_KEYS: tuple[tuple[str, tuple[str, ...], str, str, str], ...] = (
    ("openai", ("OPENAI_API_KEY", "OpenAI_KEY"), OFFICIAL_OPENAI_BASE, "openai", "openai"),
    ("anthropic", ("ANTHROPIC_API_KEY",), "", "claude", "anthropic"),
    ("claude", ("ClaudeCode_KEY",), "", "claude", "anthropic"),
    ("glm", ("GLM_KEY",), "", "glm", "openai"),
    ("qwen", ("DASHSCOPE_API_KEY",), "", "qwen", "openai"),
    ("kimi", ("MOONSHOT_API_KEY",), "", "kimi", "openai"),
    ("deepseek", ("DEEPSEEK_API_KEY", "Deepseek_KEY"), OFFICIAL_DEEPSEEK_BASE, "deepseek", "openai"),
)

PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "claude": "ClaudeCode_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "glm": "GLM_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
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
    """In-tree fixture shipped with the wheel (thin local_panel)."""
    here = Path(__file__).resolve().parent / "data" / "local_panel"
    if (here / "prices.parquet").is_file():
        return here
    return None


def panel_path() -> Path:
    raw = (os.getenv("FINAINCE_PANEL_PATH") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        parquet = path / "prices.parquet" if path.is_dir() else path
        if not parquet.is_file():
            raise ValueError(f"FINAINCE_PANEL_PATH does not exist or is not readable: {path}")
        try:
            import polars as pl
            names = pl.scan_parquet(parquet).collect_schema().names()
            if not (("ts_code" in names or "instrument" in names) and ("trade_date" in names or "datetime" in names)):
                raise ValueError(f"FINAINCE_PANEL_PATH parquet missing core columns: {path}")
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"FINAINCE_PANEL_PATH is not a valid readable parquet: {e}")
        return path
    
    packed = packaged_local_panel()
    if packed is not None:
        return packed
    return Path(__file__).resolve().parent / "data" / "local_panel"


# aiminer.core.local_data._COLUMN_ALIASES + canonical names. trade_date/ts_code
# are valid for repro_polars but not for the qlib child schema.
_QLIB_DATETIME_NAMES = {"datetime", "date", "time", "timestamp"}
_QLIB_INSTRUMENT_NAMES = {
    "instrument",
    "symbol",
    "ticker",
    "asset",
    "order_book_id",
    "code",
}


def _parquet_column_names(parquet: Path) -> list[str]:
    try:
        import polars as pl

        return list(pl.scan_parquet(parquet).collect_schema().names())
    except Exception:
        try:
            import pandas as pd

            return [str(col) for col in pd.read_parquet(parquet).columns]
        except Exception:
            return []


def local_panel_has_qlib_schema(path: Path | None) -> bool:
    """True when the parquet has columns the qlib child can canonicalize."""
    if path is None:
        return False
    parquet = path / "prices.parquet" if path.is_dir() else path
    if not parquet.is_file():
        return False
    lowered = {str(name).strip().lower() for name in _parquet_column_names(parquet)}
    return bool(lowered & _QLIB_DATETIME_NAMES) and bool(lowered & _QLIB_INSTRUMENT_NAMES)


def qlib_local_data_path() -> Path | None:
    """Prefer the configured local panel only if qlib can load it."""
    candidate = local_data_path()
    if local_panel_has_qlib_schema(candidate):
        return candidate
    try:
        packed = panel_path()
    except ValueError:
        packed = packaged_local_panel()
    if local_panel_has_qlib_schema(packed):
        return packed
    return packed or candidate


def local_data_path() -> Path | None:
    for key in ("FINAINCE_LOCAL_DATA_PATH", "LOCAL_DATA_PATH"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    if (DEFAULT_LOCAL_DATA / "prices.parquet").is_file():
        return DEFAULT_LOCAL_DATA
    try:
        return panel_path()
    except ValueError:
        return packaged_local_panel()


def raw_local_data_root() -> str:
    """Canonical dual-read of the operator-configured local data root (no fallbacks).

    Every module that needs "which directory did the operator point at" must
    call this instead of re-implementing the two-key precedence.
    """
    for key in ("FINAINCE_LOCAL_DATA_PATH", "LOCAL_DATA_PATH"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return raw
    return ""


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


def _first_env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def llm_gateway_base() -> str:
    """OpenAI-compatible gateway. Only when the operator sets a base URL."""
    return _first_env("FINAINCE_LLM_BASE_URL", "ANTHROPIC_BASE_URL").rstrip("/")


def cpa_base_url() -> str:
    """Historical name for the local OpenAI-compatible gateway."""
    return llm_gateway_base()


def cpa_api_key() -> str:
    return _first_env("ANTHROPIC_AUTH_TOKEN", "CPA_API_KEY")


def cpa_reachable(timeout: float = 1.5) -> bool:
    base = llm_gateway_base()
    if not base:
        return False
    key = cpa_api_key() or _first_env("FINAINCE_LLM_API_KEY", "LLM_API_KEY")
    url = _openai_base(base) + "/models"
    try:
        req = Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (URLError, OSError, TimeoutError, ValueError):
        return False


def official_deepseek_key() -> str:
    return _first_env("DEEPSEEK_API_KEY", "Deepseek_KEY")


def resolve_llm(*, probe: bool = False) -> dict[str, Any]:
    """Pick a chat LLM from env. No vendor is assumed."""
    load_engine_dotenv()
    provider = (os.getenv("FINAINCE_LLM_PROVIDER") or "").strip().lower()
    model = (os.getenv("FINAINCE_LLM_MODEL") or "").strip()
    explicit_base = (os.getenv("FINAINCE_LLM_BASE_URL") or "").strip()
    key = _first_env("FINAINCE_LLM_API_KEY", "LLM_API_KEY")

    gateway_base = llm_gateway_base()
    gateway_key = key or cpa_api_key()
    if gateway_key and gateway_base and (not probe or cpa_reachable()):
        name = provider or "openai"
        if name in {"", "openai", "anthropic"}:
            name = "openai"
        return {
            "via": "gateway",
            "aiminer_provider": name,
            "repro_provider": "openai",
            "api_key": gateway_key,
            "base_url": _openai_base(gateway_base),
            "model": model,
        }

    vendors = {row[0]: row for row in _VENDOR_KEYS}
    if provider:
        row = vendors.get(provider)
        if row is None:
            return {
                "via": provider if key else "missing",
                "aiminer_provider": provider,
                "repro_provider": "openai",
                "api_key": key,
                "base_url": _openai_base(explicit_base) if explicit_base else "",
                "model": model,
            }
        _name, key_names, default_base, aiminer, repro = row
        found = key or _first_env(*key_names)
        host = explicit_base or default_base
        return {
            "via": provider if found else "missing",
            "aiminer_provider": aiminer,
            "repro_provider": repro,
            "api_key": found,
            "base_url": _openai_base(host) if host else "",
            "model": model,
        }

    for name, key_names, default_base, aiminer, repro in _VENDOR_KEYS:
        found = _first_env(*key_names)
        if not found:
            continue
        host = explicit_base or default_base
        return {
            "via": name,
            "aiminer_provider": aiminer,
            "repro_provider": repro,
            "api_key": found,
            "base_url": _openai_base(host) if host else "",
            "model": model,
        }
    return {
        "via": "missing",
        "aiminer_provider": "openai",
        "repro_provider": "openai",
        "api_key": "",
        "base_url": _openai_base(explicit_base) if explicit_base else "",
        "model": model,
    }


def resolve_deepseek_llm(*, probe: bool = False) -> dict[str, Any]:
    """Alias kept for older call sites."""
    return resolve_llm(probe=probe)


def resolve_data_source() -> str:
    forced = (os.getenv("FINAINCE_DATA_SOURCE") or "").strip().lower()
    if forced in {"ricequant", "local"}:
        return forced
    if mock_llm_requested() and not os.getenv("FINAINCE_FORCE_REAL_DATA"):
        return "local"
    if has_rq_credentials():
        return "ricequant"
    return "local"


_PANEL_STATS_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def panel_stats(path: Path | None = None) -> dict[str, Any]:
    target = path or local_data_path()
    if target is None:
        return {"n_assets": 0, "n_days": 0, "thin": True, "start": None, "end": None}
    parquet = target / "prices.parquet" if target.is_dir() else target
    if not parquet.is_file():
        return {"n_assets": 0, "n_days": 0, "thin": True, "start": None, "end": None}
    try:
        st = parquet.stat()
        key = (str(parquet.resolve()), int(st.st_mtime_ns), int(st.st_size))
        hit = _PANEL_STATS_CACHE.get(key)
        if hit is not None:
            return dict(hit)
        import polars as pl

        lf = pl.scan_parquet(parquet)
        names = lf.collect_schema().names()
        code_col = "ts_code" if "ts_code" in names else "instrument"
        date_col = "trade_date" if "trade_date" in names else "datetime"
        if code_col not in names or date_col not in names:
            stats = {"n_assets": 0, "n_days": 0, "thin": True, "start": None, "end": None}
        else:
            n_assets = int(lf.select(pl.col(code_col).n_unique()).collect().item() or 0)
            n_days = int(lf.select(pl.col(date_col).n_unique()).collect().item() or 0)
            
            if n_days > 0:
                df_dates = lf.select([pl.col(date_col).min().alias("start"), pl.col(date_col).max().alias("end")]).collect()
                start_val = df_dates["start"][0]
                end_val = df_dates["end"][0]
                
                if hasattr(start_val, "date"):
                    start_val = start_val.date()
                elif start_val is not None:
                    start_val = date.fromisoformat(str(start_val)[:10])
                    
                if hasattr(end_val, "date"):
                    end_val = end_val.date()
                elif end_val is not None:
                    end_val = date.fromisoformat(str(end_val)[:10])
            else:
                start_val = None
                end_val = None
                
            stats = {
                "n_assets": n_assets,
                "n_days": n_days,
                "thin": n_assets < 50 or n_days < 60,
                "start": start_val.isoformat() if start_val else None,
                "end": end_val.isoformat() if end_val else None,
            }
        if len(_PANEL_STATS_CACHE) > 8:
            _PANEL_STATS_CACHE.clear()
        _PANEL_STATS_CACHE[key] = stats
        return dict(stats)
    except Exception:
        return {"n_assets": 0, "n_days": 0, "thin": True, "start": None, "end": None}


def local_panel_stats(path: Path | None = None) -> dict[str, Any]:
    return panel_stats(path)


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
    os.environ["LLM_API_KEY"] = key
    os.environ["FINAINCE_LLM_API_KEY"] = key
    provider = (llm.get("aiminer_provider") or "").strip().lower()
    vendor_env = PROVIDER_ENV_KEYS.get(provider)
    if vendor_env:
        os.environ[vendor_env] = key
    if llm.get("base_url"):
        os.environ["FINAINCE_LLM_BASE_URL"] = str(llm["base_url"])

# FinAlpha (`finaince`)

Single installable surface for **aiminer** factor discovery (Manager-SubAgent swarm, IC/correlation pool cull) and **reproagent** 研报复现 (ingest → reproduce/backtest → deviation self-heal → library).

Claude Code / Claude Agent SDK custom system extensions live in `finaince.sdk_ext`: in-process MCP tools (`@tool` + `create_sdk_mcp_server`) call the real discovery and reproduction functions, and a PreToolUse hook is registered on `ClaudeAgentOptions`.

## Install

Python **3.12** is the platform + reproduction path:

```bash
uv venv --python 3.12 .venv
uv pip install -e ../reproagent -e ".[reproduction]"
```

Full aiminer swarm (LangGraph / qlib / RiceQuant) stays on the **3.10** conda extra stack:

```bash
conda env create -f ../aiminer/environment.yml
conda activate aiminer
pip install -e ../aiminer[all]
```

Do not expect `pip install aiminer` (no extras) to pull qlib or langgraph.

## One-shot go-live (Topology A, Python 3.12)

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ../reproagent -e ".[reproduction]"
finaince doctor          # exit 0 only when the report's ok is true
finaince serve           # http://127.0.0.1:8000 — workbench + /api/v1
```

Open `http://127.0.0.1:8000`. `aiminer.api` may fail to import on 3.12 slim (no `psutil` / `web` extra / `PortfolioManager`); the same process still serves `/` (built workbench) and `/api/v1/*`.

`finaince doctor` prints JSON with `ok`, `home` (`FINAINCE_HOME`), `imports` (`finaince` / `aiminer` / `reproagent`), `path_hack`, and `issues`. Process exit is 0 only when `ok` is true.

## Commands

```text
finaince discover --demo|--cull-json|--swarm [--sync]
finaince reproduce PATH [--sync]
finaince catalog / eval / promote / review / jobs / doctor / serve / agent
finaince validate / library / sdk-info

`finaince agent "复现这份研报并判断能否晋升"` 启动 Claude Agent SDK 研究台：
系统提示 + 发现/复现/复核三个 specialist + 目录/求值/晋升等进程内工具。
需要本机 `claude` CLI 或凭证；没有时会返回环境失败，不会编造回测。
```

Existing `aiminer` and `reproagent` CLIs are unchanged. This package imports both trees and dispatches to them.

## Tests

```bash
# Offline (no network): CLI / catalog / eval / review / doctor / SDK
python -m pytest tests --ignore=tests/test_live_real.py

# Live: CPA DeepSeek, RiceQuant, and the three pinned categorized PDFs
# (skipped when credentials or files are missing)
python -m pytest -m live
```

## Layout

- `finaince.discovery` — `score_factor`, `cull_factor_pool`, `run_swarm`
- `finaince.reproduction` — `reproduce_report`, `validate_expression`, `search_library`
- `finaince.sdk_ext` — custom tools, PreToolUse hook, `build_claude_agent_options`
- `finaince.cli` — unified entry (`python -m finaince`)

## License

GNU Affero General Public License v3.0 (`LICENSE`). Same family as `aiminer`.

# FinAlpha 产品手册

安装包名是 `finaince`，产品名是 **FinAlpha**。这份手册对应公开仓库 [`WeissHymmnos/finaince`](https://github.com/WeissHymmnos/finaince) 的 `main`。

文中截图都是本机 `finaince serve`（监听 `127.0.0.1`，已填 desk token）对着真实 catalog 和 review 队列拍的。

许可证：[AGPL-3.0](../LICENSE)。版权和第三方引擎见 [NOTICE](../NOTICE)。

---

## 1. 这是什么

FinAlpha 管卖方研报里的选股因子。你在本机打开它，把候选收进 **catalog**，在本地行情上 **eval**，过了门禁再 **promote → review → pool**。研报本身用 `reproagent` 和 `finpdfpro` 抽公式、再回测。

包装里带了两只股票的 `local_panel`，日期从 2023-01-03 到 2023-02-10，成本 0 bps，表达式是 `Rank(Delta(close, 1))`。`finaince baseline` 报的 IC、Sharpe 只对这几条数据负责。股票太少，够用来确认安装跑通。

---

## 2. 安装（Python 3.12）

克隆这一个公开仓库就够。复现和发现引擎从 GitHub 上钉死的 commit 安装，具体 SHA 写在 `pyproject.toml` 的 extras 里。

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
cp .env.example .env
# 编辑 .env，至少写上：FINAINCE_DESK_TOKEN=你自己的口令
finaince doctor
```

`doctor` 只有在 JSON 里 `"ok": true` 时才退出 0。装完后下面三行 import 都要成功：

```python
import finaince, reproagent
from aiminer.manager import cull_alpha_pool
```

可选 extras：

| extra | 用途 |
|---|---|
| `.[reproduction]` | 复现、catalog、eval。公开安装先装这个 |
| `.[discovery]` | aiminer 发现 |
| `.[agent]` | Claude Agent SDK |
| `.[dev]` | pytest |
| `.[all]` | 上面全部 |

完整工作台（Catalog、Review 那些页）需要一份编好的 `aiminer/frontend/dist`。wheel 里只有一张简单的 `finaince/web/index.html`。在作者本机，`finaince serve` 会先找旁边的 `aiminer/frontend/dist`。设 `FINAINCE_PACKAGED_SPA=1` 会只用那张简单页。

---

## 3. 十五分钟走通一遍

下面每条都是真实 CLI。手册里的截图，就是在同一套 home 上跑完这条路径之后拍的。

### 3.1 先看健康状况

```bash
export FINAINCE_DATA_SOURCE=local
export ALLOW_MOCK_LLM=true          # 离线复现用；已经接了聊天模型就不要设
export FINAINCE_DESK_TOKEN=desk-local
finaince doctor
```

正常时 `"ok"` 为 true，`"imports"` 里 `finaince`、`aiminer`、`reproagent` 都是 true，`qlib_child.via` 是 `qlib_child`。

### 3.2 跑一遍自带的 baseline

```bash
finaince baseline
```

数字跟打包 panel 绑定。连续跑两次，`ok` 和 IC、Sharpe 必须对得上。一次典型输出如下：

```json
{
  "ok": true,
  "window": {
    "start": "2023-01-03",
    "end": "2023-02-10",
    "universe": "local_panel",
    "cost_bps": 0,
    "dialect": "repro_polars",
    "expression": "Rank(Delta(close, 1))",
    "note": "install smoke local_panel fixture; cost 0 bps"
  },
  "metrics": {
    "ic_mean": -0.18518518518518517,
    "sharpe_ratio": -3.7648821410668716,
    "rows": 56,
    "universe_claim": "local_panel",
    "transaction_cost_bps": 0.0
  },
  "claim": "install smoke local_panel fixture; cost 0 bps"
}
```

### 3.3 同一条表达式走评测路由

```bash
finaince eval "Rank(Delta(close, 1))" --dialect repro_polars --backend local
finaince eval 'Rank($close)' --dialect qlib --backend local
```

在 3.12 slim 安装上，`qlib` 走进程内的 `qlib_child`。如果你机器上的 `~/Documents/Data/prices.parquet` 只有 `trade_date` / `ts_code` 两列，评测会改用打包的 `local_panel`。

### 3.4 用隔离的 `compute(panel)` 写入 catalog

```bash
finaince impl examples/15min/compute.py --name rank_delta --universe local_panel
```

成功时 JSON 里会有 `catalog_id`（例如 `fac_iso_…`），表达式是 `Rank(Delta(close, 1))`。`daily_returns` 来自台上同一条 `evaluate` 路径，日期跟打包 panel 对齐。

### 3.5 复现仓库里的 sample PDF

```bash
export FINAINCE_PDF_ROOT=/path/to/reproagent/tests/fixtures/sample_reports
finaince reproduce "$FINAINCE_PDF_ROOT/minimal.pdf" --sync
```

离线且开了 `ALLOW_MOCK_LLM=true` 时走 mock 抽取。写手册那天跑通的结果是 `"status": "passed"`，因子名 `mock_momentum`，公式 `close / Ref(close, 5) - 1`。没有 LLM 时，也可能停在 `review_enqueued`，并带上 `formula_proxy`。那是管道走完之后的诚实终态。

### 3.6 提交审核；股票太少时用 CLI 放行

```bash
finaince catalog
finaince promote '<catalog_id>' --to to_pool --yes
finaince review
finaince review --approve '<promotion_id>' --override thin_panel
```

打包的 `local_panel` 只有两只股票，晋升时会打上 `thin_panel`。HTTP `POST /api/v1/review/{id}/approve` 如果带了 `override`，固定返回 403。这两只股票要进 pool，请用上面最后那一行 CLI。

### 3.7 打开工作台

```bash
finaince serve --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。左侧 **API Token** 填 `.env` 里同一个 `FINAINCE_DESK_TOKEN`。空着不填的话，catalog 读接口会 401：

![未填 token 时 Catalog 返回 401](handbook/images/12-catalog-no-token.png)

如果要把服务绑到 `0.0.0.0`，还要同时设置 `FINAINCE_ALLOW_PUBLIC_BIND=1`。

---

## 4. 工作台怎么用

左侧导航是 Catalog、Review、Reproduce、Agent、Swarm Runs、Alpha Pool、Manual Backtest、Strategy Backtest、Wiki、Operations。Token 存在浏览器的 `localStorage` 里，请求会同时带上 `Authorization: Bearer …` 和 `X-API-Key`。

### 4.1 Catalog

发现和复现共用这一张索引。手册环境里有两行：研报复现写进去的 `mock_momentum`（状态是 `review`），以及隔离 impl 写进去的 `rank_delta`（状态是 `candidate`）。

![Catalog 列表](handbook/images/01-catalog.png)

点进一行，能看到表达式、universe、`thin_panel` 标记，还有「提交晋升」。HTTP 晋升只进入 review 队列，不会直接写入 pool。

![Catalog 详情与晋升](handbook/images/02-catalog-detail.png)

### 4.2 Review

这里列的是待审晋升。图里这一条方向是 `to_pool`、状态是 `pending`，门禁失败原因是 `thin_panel`。页面上的 **Approve** 不会附带 override，点下去仍会失败。要在股票不足 20 只时放行，请回到终端执行 `finaince review --approve … --override thin_panel`。

![Review 队列因 thin_panel 拒批](handbook/images/03-review.png)

### 4.3 Reproduce

走 HTTP 时，`pdf_path` 必须落在 `FINAINCE_PDF_ROOT` 下面，否则 403。命令行的 `finaince reproduce` 读的是本机文件路径，不受这条 HTTP 白名单约束。

![Reproduce](handbook/images/04-reproduce.png)

### 4.4 Agent

把自然语言交给 desk，让它去调 catalog、eval、reproduce、review。它不会自己编一套回测数字。要跑完整对话，需要安装 `.[agent]`，并且本机有 `claude` CLI。

![Agent](handbook/images/05-agent.png)

### 4.5 Swarm、Pool、回测、Wiki、运维

这几页来自同源挂上的 aiminer 工作台。Pool 里能看到 catalog 对偶写进去的 `mock_momentum` 和 `rank_delta`，IC 与 catalog 一致。Swarm、Manual、Strategy 要接上真实行情或 live 凭据，才有研究上的意义。

![Swarm Runs](handbook/images/06-swarm-runs.png)

![Alpha Pool](handbook/images/07-alpha-pool.png)

![Manual Backtest](handbook/images/08-manual-backtest.png)

![Strategy Backtest](handbook/images/09-strategy-backtest.png)

![Wiki](handbook/images/10-wiki.png)

![Operations](handbook/images/11-operations.png)

---

## 5. 命令一览

```text
finaince --help
```

| 命令 | 做什么 |
|---|---|
| `doctor` | 检查家目录、import、panel、qlib child、LLM（`--audit-check` 重放审计哈希链；`--watch/--iterations/--interval` 常驻） |
| `baseline` | 用自带的两只股票跑一遍固定窗口（`run_locked_baseline(cost_bps=…)` 支持含成本口径） |
| `eval EXPR --dialect repro_polars\|qlib --backend local\|ricequant\|auto` | 按方言和数据后端评测；`--start/--end` 锁窗；`--snapshot` 对金标快照（漂移只告警）；`--engine-parity` 可选 3.10 qlib 子进程对拍。成本参数 `cost_bps` 目前是 `EvalRequest` 编程接口，CLI/HTTP 未暴露 |
| `validate EXPR` | 用 polars 引擎校验表达式 |
| `impl PATH.py --name NAME --universe local_panel` | 隔离执行 `compute(panel)`，写入 catalog |
| `reproduce PDF --sync [--start --end --source]` | 摄入研报并回测，可锁窗、选数据源 |
| `catalog [--source SRC] [--rebuild] [--retag-synthetic]` | 列出 / 重建 catalog（rustminer 只读源） |
| `library [-q QUERY] [--style STYLE]` | 先搜 catalog（可按 style 过滤），再搜引擎库 |
| `promote ID --to to_pool --yes` | 提交审核 |
| `review` / `--approve ID --override thin_panel,weak_ic` / `--reject ID` | 查看队列、放行（override 仅限列名门禁）、退回 candidate |
| `discover --demo` | 不调 LLM，做一轮 IC / 相关 cull |
| `discover --swarm --sync/--async` | 启动 aiminer manager swarm（需要 LLM）；裸 `discover` 退出码 2 |
| `serve --host 127.0.0.1 --port 8000` | 打开同源工作台 |
| `jobs [--cancel ID]` | 作业列表 / 取消 |
| `trace [--limit N]` | 研究事件链（每条带 hypothesis） |
| `loop [--steps N] [--expression EXPR]... [--sync/--async] [--workers N]` | 因子/模型交替环，支持表达式队列批量；模型步自动尝试跨 ready 因子动态组合（WS-I）；`--workers N`（1–8）对队列做进程级并行预评估 |
| `bench [--is-start --is-end --oos-start --oos-end --cost-bps] [--sync]` | WS-D：CSI300 point-in-time 双窗基准表（IS 2019–2023 / OOS 2024，双边 5bps）；`--sync` 需米筐凭据先抓缓存。已跑通的首表（2026-08）：rank_delta_20 IS IC +0.0038 / OOS +0.0113；reversal_5 OOS 净 Sharpe +0.54 对 IS −0.62 反号照实报；种子因子为管线验证器非 alpha 主张 |
| `campaign --root DIR [--limit N] [--stats] [--reset-failed]` | WS-K：研报语料批量治理内复现，manifest 断点续跑；`no_factors` 是诚实终态 |
| `brain-submit EXPR [--catalog-id ID]` | WS-J：把治理流产出提交 BRAIN 外部裁决；无凭据时诚实降级为内部双窗基准 |
| `agent PROMPT --max-turns N` | Claude Agent desk 一轮研究 |
| `sdk-info` / `sdk-query --prompt` | SDK 会话信息 / 直查 |

---

## 6. 门禁

往 pool 里晋升时，下面这些会拦住：

| 门禁 | 何时触发 |
|---|---|
| `simulated` | 因子被判定为模拟指标 |
| `formula_proxy` | 复现用了公式代理 |
| `thin_panel` | 载入的 panel 股票数少于 20，和 universe 字符串无关 |
| `missing_ic` | IC 缺失，或不是有限数字 |
| `ic_threshold` | 有限 IC 的绝对值不超过 0.005 |
| `missing_returns` | 没有日收益 |
| `correlated` | 收益相关超过 0.7：to_pool 对照 pool 里已有因子；to_library 还会对照 catalog 里带日收益的 reproduction 行 |
| `empty_code` | to_pool 时 qlib 表达式为空 |
| `weak_ic` | Harvey-Liu t 统计量：\|ICIR\|·√n_days < 3.0（样本不足时跳过并记 `insufficient_for_t_stat`） |
| `inflated_sharpe` | Deflated Sharpe < 0.95（Bailey–López de Prado；试验次数从 trace 的 eval/impl 事件数估计，少于 20 个观测跳过） |
| `homogeneous` | WS-A 结构查重：与种子库/catalog 行的最大 AST 子树相似度 > 0.85（表达式解析失败时放行，由正则校验兜底） |
| `overcomplex` | WS-A 复杂度上限：符号长度 > 40 或自由参数 > 6 或特征数 > 8（阈值先宽后紧） |
| `corr_error:{exc}` | 相关性检查本身抛错，fail-closed |

`weak_ic`、`inflated_sharpe`、`homogeneous`、`overcomplex` 和 `thin_panel` 一样可以用 CLI `--override` 放行，审计日志会记一笔。HTTP 拒绝一切 `override`。

生成端正则化（WS-A）：候选入池前在 `cull_factor_pool` 做结构去重——近重复表达式（规范化后等价或相似度 > 0.85）直接淘汰并记 reason；catalog upsert 时写 `expr_hash` 列支持 O(1) 查重。sign-reflection（WS-C）：负 IC 且 \|t\|≥3 的候选生成镜像行一并评分。

---

## 6.1 研究循环与对抗评审

- `finaince loop` 让因子步和模型步交替：因子步按表达式队列逐条评测（`--expression` 可重复），模型步用可切换预测头（环境变量 `FINAINCE_MODEL_HEAD=ols|gbm`，GBM 走 expanding-window walk-forward，库缺失诚实跳过）。`--sync/--async` 决定是否走 JobRunner 子进程。
- `FINAINCE_LOOP_ADVISOR=1` 时动作选择先问聊天 LLM（读最近 trace 历史 + 低相关样例 + 失败教训）；任何失败都回落启发式并记录 `advisor_error`。
- SDK 工具 `research_context` 返回 CSS 式低相关样例与失败教训，playbook 要求提出新因子前先调它。
- 晋升可选对抗评审：`review --approve ID` 加 adversary 开关或 `POST /api/v1/review/{id}/adversary`。它在**子进程全新上下文里重跑同一条评测**，比对 IC/Sharpe 容差、检查 proxy 与收益存在性；拒绝则 veto，行留在 review。默认关。

## 6.2 超越型工作流（v3 计划落地件）

- **WS-H 代码因子进化**（`code_evolution.py`）：LLM 写完整 `compute(panel)` → bwrap/冻结沙箱子进程执行 → shipped eval + 全门禁；失败按 error 前缀检索相似教训 + AST-diff 编辑动机改写下一稿。无 LLM 时停在 `llm_unavailable`，绝不假装迭代。
- **WS-I 动态组合**（`combination.py`）：跨 catalog ready 因子按滚动逆波动率做每日权重组合（只用 t-1 前信息），换手付双边成本；loop 模型步自动尝试，bandit 奖励升级为净 Sharpe。
- **WS-L 过程记忆**（`process_memory.py`）：信用分配用「门禁+对抗存活」而非原始残差；AST-diff 编辑动机按错误类加权记忆注入 advisor prompt；经验链作为展示层（链尾扩展须 RankIC 超全链）。
- **WS-F 工作台**：wheel 内 stub SPA 升级为三栏（Catalog / Review 队列含 gates 报告与对抗按钮 / Trace 时间线），纯静态零构建链。

---

## 7. HTTP 接口

默认听在 `127.0.0.1:8000`。CORS 默认只放行 localhost 的 8000 和 5173。

| 路径 | 鉴权 |
|---|---|
| `GET /`、`GET /api/v1/health`、`GET /api/v1/baseline` | 公开 |
| `GET /api/v1/catalog`、`/catalog/{id}`（可带 `?embed=memory`）、`/review`、`/jobs`、`/jobs/{id}`、`/audit`、`/trace` | 需要 desk token |
| `GET /api/v1/review/{id}/gates` | 只读门禁报告（不改状态），需要 desk token |
| `GET /api/v1/bench` | WS-D 双窗基准表（JSON，含 provenance 与 Markdown），需要 desk token |
| `POST` promote / eval / approve / reject / adversary / reproduce / impl / impl/needs / agent / loop | 需要 desk token |
| `POST /api/v1/jobs/{id}/cancel` | 需要 desk token |
| `POST /api/v1/review/{id}/approve` 且 body 带 `{"override":[…]}` | **403** |
| `POST /api/v1/review/{id}/approve` 且 body 带 `{"adversary":true}` | 允许；对抗评审拒绝时返回 `adversary_rejected`，行留在 review |
| `POST /api/v1/review/{id}/adversary` | 只出 fresh-context 重评报告，不做决定 |
| `POST /api/v1/loop` body 可带 `{"expressions":[…]}`、`{"sync":false}` 与 `{"workers":1..8}` | 批量表达式队列 + 异步 job + 批内并行（worker 级失败按单表达式诚实落败） |
| `POST /api/v1/reproduce` 且 PDF 不在 `FINAINCE_PDF_ROOT` 下 | **403** |

Token 放在 `Authorization: Bearer <token>` 或 `X-API-Key: <token>`。`FINAINCE_DESK_TOKEN` 会同步到 `AIMINER_AUTH_TOKEN`，aiminer 的 `/api/*` 写操作同样要带这个口令。

---

## 8. 环境变量

先复制 [`.env.example`](../.env.example)。常用项如下：

| 变量 | 作用 |
|---|---|
| `FINAINCE_DESK_TOKEN` | 工作台和写操作 HTTP |
| `FINAINCE_DATA_SOURCE=local` | 走本地 panel |
| `FINAINCE_PDF_ROOT` | HTTP 复现允许的目录 |
| `FINAINCE_PACKAGED_SPA=1` | 只用 wheel 里那张简单首页 |
| `FINAINCE_ALLOW_PUBLIC_BIND=1` | 允许绑定 `0.0.0.0` |
| `FINAINCE_PATH_HACK=1` | 使用作者树旁边的源码 |
| `FINAINCE_NO_PATH_HACK=1` | CI 和陌生人安装时关掉旁路 |
| `ALLOW_MOCK_LLM=true` | 离线 mock 抽取 |
| `RQ_TOKEN` / `RQ_USER`+`RQ_PASS` | 米筐，可选 |
| `FINAINCE_LLM_PROVIDER` / `FINAINCE_LLM_MODEL` / `FINAINCE_LLM_API_KEY` / `FINAINCE_LLM_BASE_URL` | 聊天模型，可选。不默认任何厂商 |
| `FINAINCE_LIVE_AGENT=1` | 打开 live Claude 测试 |
| `FINAINCE_PANEL_PATH` | 指向完整行情 parquet（列结构与自带 prices.parquet 一致），替换 smoke 面板 |
| `FINAINCE_BT_START` / `FINAINCE_BT_END` | 覆盖默认回测窗 |
| `FINAINCE_LOCAL_DATA_PATH` | 本地数据目录（qlib child 也读） |
| `FINAINCE_CATALOG=0` | 关掉引擎→catalog 双写 hook |
| `FINAINCE_CATALOG_MEMORY=1` | catalog 详情默认 embed memory 摘要 |
| `FINAINCE_LOOP_ADVISOR=1` | loop 动作选择先问聊天 LLM，失败回落启发式 |
| `FINAINCE_MODEL_HEAD=ols\|gbm` | loop 模型头选择；gbm 需装 `.[gbm]` |
| `FINAINCE_PANEL_CACHE=off` | 关掉 WS-C 进程级 panel 缓存（默认开，仅 local 后端） |
| `FINAINCE_MAX_JOBS=N` | 异步子进程并发上限（默认 2，超限返回 `max_jobs_reached`） |
| `FINAINCE_SANDBOX=bwrap\|auto\|off` | WS-E 沙箱层：auto 检测到 bwrap 即用；失败自动回落冻结内建并标注 `sandbox_fallback` |
| `BRAIN_USER` / `BRAIN_PASS` | WS-J BRAIN 外部裁决凭据；缺失时降级为内部双窗基准并在输出声明 |
| `FINAINCE_QLIB_SUBPROCESS=1` | qlib 评测走 3.10 子进程（配 `AIMINER_PYTHON`） |
| `FINAINCE_QLIB_BACKEND` / `FINAINCE_QLIB_TIMEOUT` | 子进程 qlib 的数据后端与超时 |
| `AIMINER_PYTHON` | 3.10 conda 解释器路径（swarm / qlib 子进程） |
| `AIMINER_INCLUDE_SPA=1` | 允许挂 aiminer 前端 dist |
| `FINAINCE_JOB_ID` | 异步子进程回写同一 job 行（内部使用） |

---

## 9. 测试

```bash
python -m pytest tests --ignore=tests/test_live_real.py
```

GitHub Actions 跑两份：`packaging-312`（公开 extras，并设置 `FINAINCE_NO_PATH_HACK=1`）和 `pytest-offline`。米筐、券商 PDF、Claude 属于 `pytest -m live`，没有凭据就不用跑。

---

## 10. 能做什么，还要另备什么

clone 下来之后，本机就能跑 catalog、eval、CLI review，复现仓库里的 sample PDF，以及 3.12 上的 qlib child。

要拿它评真正的因子，还得自己准备：编好的前端 dist、至少 20 只股票的行情，米筐或本地数据都行。要从券商 PDF 里抽公式、跑 swarm，还需要 LLM。

如果把 `baseline` 的 IC / Sharpe 写进对外材料，请一并带上 JSON 里 `claim` 那一句，说明数据来自自带的两只股票、成本 0 bps。

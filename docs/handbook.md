# FinAlpha 产品手册

安装包名是 `finaince`，产品名是 **FinAlpha**。这份手册对应公开仓库 [`WeissHymmnos/finaince`](https://github.com/WeissHymmnos/finaince) 的 `main`。

文中截图都是本机 `finaince serve`（监听 `127.0.0.1`，已填 desk token）对着真实 catalog 和 review 队列拍的。

许可证：[AGPL-3.0](../LICENSE)。版权和第三方引擎见 [NOTICE](../NOTICE)。

---

## 1. 这是什么

FinAlpha 管卖方研报里的选股因子。你在本机打开它，把候选收进 **catalog**，在本地行情上 **eval**，过了门禁再 **promote → review → pool**。研报本身用 `reproagent` 和 `finpdfpro` 抽公式、再回测。

随包装了一份两只股票的 `local_panel`，窗口是 2023-01-03 到 2023-02-10，成本 0 bps，表达式是 `Rank(Delta(close, 1))`。`finaince baseline` 报出来的 IC、Sharpe 只对这份夹具负责。样本很薄，用来确认安装跑通就行。

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

完整工作台（Catalog、Review 那些页）需要一份编好的 `aiminer/frontend/dist`。wheel 里带的 `finaince/web/index.html` 只是占位。在作者本机，`finaince serve` 会先找旁边的 `aiminer/frontend/dist`。若要强制用占位页，设 `FINAINCE_PACKAGED_SPA=1`。

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

### 3.2 跑安装冒烟 baseline

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

### 3.6 提交审核，再用 CLI 放行薄面板

```bash
finaince catalog
finaince promote '<catalog_id>' --to to_pool --yes
finaince review
finaince review --approve '<promotion_id>' --override thin_panel
```

打包 panel 只有两只股票，晋升时会打上 `thin_panel`。HTTP `POST /api/v1/review/{id}/approve` 如果带了 `override`，固定返回 403。冒烟面板要进 pool，请用上面最后那一行 CLI。

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

这里列的是待审晋升。图里这一条方向是 `to_pool`、状态是 `pending`，门禁失败原因是 `thin_panel`。页面上的 **Approve** 不会附带 override，点下去仍会失败。要放行薄面板，请回到终端执行 `finaince review --approve … --override thin_panel`。

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
| `doctor` | 检查家目录、import、panel、qlib child、LLM |
| `baseline` | 跑锁定的安装冒烟窗口 |
| `eval EXPR --dialect repro_polars\|qlib --backend local\|ricequant` | 按方言和数据后端评测 |
| `validate EXPR` | 用 polars 引擎校验表达式 |
| `impl PATH.py --name … --universe local_panel` | 隔离执行 `compute(panel)`，写入 catalog |
| `reproduce PDF --sync` | 摄入研报并回测 |
| `catalog` | 列出 catalog |
| `library` | 先搜 catalog，再搜引擎库 |
| `promote ID --to to_pool --yes` | 提交审核 |
| `review` / `review --approve ID --override thin_panel` | 查看队列，或放行一条 |
| `discover --demo` | 不调 LLM，做一轮 IC / 相关 cull |
| `discover --swarm` | 启动 aiminer manager swarm（需要 LLM） |
| `serve --host 127.0.0.1 --port 8000` | 打开同源工作台 |
| `jobs` / `trace` / `loop` | 作业、事件链、薄组合环 |
| `agent` / `sdk-info` / `sdk-query` | Claude Agent desk |

---

## 6. 门禁

往 pool 里晋升时，下面这些会拦住：

| 门禁 | 何时触发 |
|---|---|
| `thin_panel` | 载入的 panel 股票数少于 20，和 universe 字符串无关 |
| `formula_proxy` | 复现用了公式代理 |
| `missing_ic` | IC 缺失，或不是有限数字 |
| `missing_returns` | 没有日收益 |
| `weak_ic` | 有限 IC 的绝对值不超过 0.005 |
| `correlated` | 与 pool 里已有因子的收益相关超过 0.7 |

CLI 可以用 `--override thin_panel`，审计日志会记一笔。HTTP 拒绝一切 `override`。

---

## 7. HTTP 接口

默认听在 `127.0.0.1:8000`。CORS 默认只放行 localhost 的 8000 和 5173。

| 路径 | 鉴权 |
|---|---|
| `GET /`、`GET /api/v1/health`、`GET /api/v1/baseline` | 公开 |
| `GET /api/v1/catalog`、`/review`、`/jobs`、`/audit`、`/trace` | 需要 desk token |
| `POST` promote / eval / approve / reject / reproduce / impl / agent / loop | 需要 desk token |
| `POST /api/v1/review/{id}/approve` 且 body 带 `{"override":[…]}` | **403** |
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
| `FINAINCE_PACKAGED_SPA=1` | 强制使用占位页 |
| `FINAINCE_ALLOW_PUBLIC_BIND=1` | 允许绑定 `0.0.0.0` |
| `FINAINCE_PATH_HACK=1` | 使用作者树旁边的源码 |
| `FINAINCE_NO_PATH_HACK=1` | CI 和陌生人安装时关掉旁路 |
| `ALLOW_MOCK_LLM=true` | 离线 mock 抽取 |
| `RQ_TOKEN` / `RQ_USER`+`RQ_PASS` | 米筐，可选 |
| `FINAINCE_LLM_PROVIDER` / `FINAINCE_LLM_MODEL` / `FINAINCE_LLM_API_KEY` / `FINAINCE_LLM_BASE_URL` | 聊天模型，可选。不默认任何厂商 |
| `FINAINCE_LIVE_AGENT=1` | 打开 live Claude 测试 |

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

对外引用冒烟 IC / Sharpe 时，请一并带上 `claim` 里的那句：*install smoke local_panel fixture; cost 0 bps*。

# FinAlpha

[English](README.md) · **中文**

FinAlpha（Python 包名 `finaince`）是一个面向 A 股选股因子研究与卖方研报复现的本地工作台。它把候选因子纳入本地 SQLite 目录（Catalog）统一管理，在明确区分数据溯源的评测核上计算指标，通过默认拒止（fail-closed）的门禁与独立子进程对抗重评控制因子入池，并结合 `reproagent` 与 `finpdfpro` 从券商 PDF 研报中抽取公式与回测结果。

- **[产品手册](docs/handbook.md)** — 完整命令行教程、工作台界面截图、HTTP 接口契约与环境变量字典。
- **[架构与评测核说明](docs/architecture.md)** — 三台评测核的边界、目录写入规则与可引用数据的判定标准。

![填好 desk token 后的 Catalog 界面](docs/handbook/images/01-catalog.png)

---

## 核心设计原则

1. **门禁默认拒止（Fail-Closed）**：候选因子向因子池（Pool）或因子库（Library）晋升时，只要触发样本股票不足 20 只（`thin_panel`）、使用公式占位代理（`formula_proxy`）、评测核不可引用（`not_citable`）、多重检验显著性不足（`weak_ic`、`inflated_sharpe`）或 AST 结构同质化/过复杂（`homogeneous`、`overcomplex`），晋升即被拦截。
2. **审批强制对抗重评（Adversarial Re-evaluation）**：执行 `approve` 时，系统默认拉起全新子进程独立重跑评测，核验 IC 与 Sharpe 是否在容差内一致。HTTP 接口严禁跳过对抗重评，也不接受任何 `override` 绕过参数。
3. **严格区分安装探针与实证数据**：仓库内置的两只股票面板 `local_panel`（2023-01-03 至 2023-02-10，0 bps 成本）仅用于验证本地安装与算子链路完好（`finaince baseline`）。真实因子评测需使用点时（Point-in-Time）CSI300 缓存（`finaince bench`）或通过 `FINAINCE_PANEL_PATH` / 米筐（RiceQuant）接入完整行情。

---

## 安装指南（Python 3.12）

直接克隆本仓库并通过 `uv` 安装。复现与挖掘引擎均按 `pyproject.toml` 中锁定的公开 GitHub commit 自动拉取，不依赖作者本机目录。

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
cp .env.example .env   # 启动服务前请在 .env 中配置 FINAINCE_DESK_TOKEN
finaince doctor
```

`finaince doctor` 会依次检查 Python 依赖导入（`finaince`、`reproagent`、`aiminer`）、数据库路径、内置行情面板与 Qlib 子进程，全部通过时 JSON 中的 `ok` 为 `true` 且退出码为 `0`。

可选依赖组（extras）：
- `.[discovery]`：`aiminer` 多智能体因子挖掘
- `.[gbm]`：LightGBM 曲线择时模型头
- `.[agent]`：Claude Agent SDK 研究台对话集成
- `.[dev]`：`pytest`、`pytest-cov`、`ruff`
- `.[all]`：安装上述全部扩展

`finaince serve` 默认仅监听 `127.0.0.1:8000`；除 `/api/v1/health` 与 `/api/v1/baseline` 外，所有 `/api/v1` 读写接口均需携带 `FINAINCE_DESK_TOKEN`。如需监听 `0.0.0.0`，必须显式设置 `FINAINCE_ALLOW_PUBLIC_BIND=1`。安装包内置了零构建依赖的三栏式轻量工作台（`finaince/web/index.html`）；若同级目录存在编译好的 `aiminer/frontend/dist`，服务会自动挂载完整前端（可通过 `FINAINCE_PACKAGED_SPA=1` 强制使用内置页）。

---

## 十五分钟快速走通

以下命令全部基于包内自带的 `local_panel`（两只股票）运行，图文对照见 [docs/handbook.md](docs/handbook.md)：

```bash
# 1. 环境自检与安装探针基准
finaince doctor
finaince baseline

# 2. 评测因子表达式（支持 repro_polars 与 qlib 两种方言）
finaince eval "Rank(Delta(close, 1))" --dialect repro_polars --backend local
finaince eval 'Rank($close)' --dialect qlib --backend local

# 3. 在沙箱中运行自定义 Python 因子并写入 Catalog
finaince impl examples/15min/compute.py --name rank_delta --universe local_panel

# 4. 提交晋升申请并查看待审队列
finaince promote '<catalog_id>' --to to_pool --yes
finaince review

# 5. 审批晋升（默认触发子进程对抗重评）
finaince review --approve '<promotion_id>' --override thin_panel

# 6. 启动本地 Web 工作台
finaince serve --host 127.0.0.1 --port 8000
```

关于第 5 步的说明：`--override thin_panel` 仅豁免“股票数不少于 20 只”这一项门禁，便于在烟雾测试面板上走通命令流；但由于两只股票的 `engine_native` 回测结果标记为 `citable=False`，不可豁免的 `not_citable` 门禁仍会阻止该测试记录进入正式因子池。

---

## 评测核与数据口径

FinAlpha 区分三台评测核（`pit_core`、`engine_native`、`qlib`），详见 [docs/architecture.md](docs/architecture.md)。

### 1. 安装探针基准（`finaince baseline`）

`finaince baseline`（对应 `run_locked_baseline()`）锁死在内置的两只股票面板上，仅用于确认计算结果确定可复现：

| 参数 | 设定值 |
|---|---|
| 时间窗口 | `2023-01-03` → `2023-02-10` |
| 股票池 | `local_panel`（2 只股票，56 行） |
| 交易成本 | `0 bps` |
| 因子表达式 | `Rank(Delta(close, 1))` |
| 方言 | `repro_polars` |

连续运行两次 `finaince baseline`，输出的 `ok` 状态、IC 与 Sharpe 必须完全一致。对外引用任何数字时，必须注明其来自 `local_panel`（0 bps）还是真实历史股票池。

### 2. 点时 CSI300 实证轨（`finaince bench` 与 `--backend pit`）

要在真实股票池上做可引用的截面评测：
- 配置米筐凭证（`RQ_USER` / `RQ_PASS` 或 `RQ_TOKEN`），执行 `finaince bench --sync` 拉取点时 CSI300 历史缓存（按年存储 Parquet 并校验 `sha256`，按每年 6 月/12 月第二个周五切换成分股，采用后复权价格，自动过滤次日停牌与一字涨跌停不可交易标的）。
- 或将 `FINAINCE_PANEL_PATH` 指向符合 `prices.parquet` 列结构（`trade_date`、`ts_code`、`open`、`high`、`low`、`close`、`volume`、`amount`）的本地 Parquet 行情文件。

`finaince bench` 与 `finaince eval --backend pit` 共用同一套 Polars 向量化点时核（`engine=pit_core`），默认切分 IS（2019–2023）与 OOS（2024）双窗口，按五分位多空组合换手率扣除双边成本并剔除窗口末日防止跨窗泄露。

---

## 功能概览

- **卖方研报复现与批量战役（`reproduce` / `campaign`）**：调用 `finpdfpro` 解析研报版面与公式，经 `reproagent` 回测验证；支持对整个研报目录发起断点续跑的批量复现战役，对不含可测因子的研报诚实记为 `no_factors`。
- **多重检验与结构去重门禁（`review/gates.py`）**：除数据溯源与样本量检查外，内置 Harvey–Liu $t$ 检验（$|t| \ge 3.0$）、Bailey–López de Prado 缩减夏普比率（DSR $\ge 0.95$）、AST 规范化哈希查重（`expr_hash`）与子树相似度门禁（`homogeneous`）、公式复杂度上限（`overcomplex`）以及显著负 IC 因子的符号镜像反转。
- **算子模板网格筛选与逐年折叠（`sweep` / `wf-candidates`）**：在 `pit_core` 上并行扫描算子模板 $\times$ 字段 $\times$ 窗口网格，支持按家族配额提取 Top 候选并做逐年 Walk-Forward 稳健性检验与截面名次合成。
- **AST 白名单与 `bwrap` 沙箱代码进化（`isolate.py` / `code_evolution.py`）**：对自定义 `compute(panel)` 代码先做静态 AST 白名单审查，再通过无网络的 `bwrap --unshare-all`（或冻结内建命名空间）隔离执行；失败时按错误前缀检索历史教训辅助改写。
- **无前瞻动态因子组合（`combination.py`）**：对已入池的 `pit_core` 因子日收益序列按严格因果（仅用 $t-1$ 及之前信息）的滚动逆波动率加权，并按每日权重 L1 换手扣除双边交易成本。
- **研究循环与过程记忆（`loop.py` / `process_memory.py`）**：因子步与 Walk-Forward 曲线择时头（`FINAINCE_MODEL_HEAD=ols|gbm`）交替运行；只有同时通过门禁与对抗重评的候选，其 AST 编辑动机才会被计入过程记忆正向信用。

关于 FinAlpha 与微软 RD-Agent 等无人值守演化框架的定位差异，可参考历史调研笔记 [docs/rdagent-gap.md](docs/rdagent-gap.md) 与 [docs/competitor-analysis.md](docs/competitor-analysis.md)。

---

## 测试与代码质量

运行全量离线测试（无需联网或外部 API 密钥）：

```bash
uv run pytest -q -m "not live"
```

检查核心治理模块的测试覆盖率（CI 强制要求不低于 82%）：

```bash
uv run pytest tests --ignore=tests/test_live_real.py \
  --cov=finaince.expr_ast --cov=finaince.db --cov=finaince.data_track \
  --cov=finaince.combination --cov=finaince.code_evolution --cov=finaince.corpus_campaign \
  --cov=finaince.brain_track --cov=finaince.process_memory --cov=finaince.domain.scoring
```

涉及米筐实盘拉数、真实 PDF 解析与在线大模型的测试已标记为 `live`，按需通过 `uv run pytest -m live` 触发。

---

## 贡献与许可证

开发环境搭建与 PR 规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

本项目基于 [GNU Affero General Public License v3.0](LICENSE) 发布，版权与第三方引擎声明见 [NOTICE](NOTICE)。

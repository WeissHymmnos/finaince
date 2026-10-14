# FinAlpha 架构与评测核说明

| 属性 | 说明 |
|---|---|
| 更新日期 | 2026-10-14 |
| 适用范围 | `finaince` 0.1.0（产品名 **FinAlpha**）当前主分支 |
| 定位 | 定义系统内三台评测核的职责边界、Catalog 写入权、晋升门禁规则与可引用数据的判定标准 |

执行 `finaince doctor` 时，输出中的 `evaluation_cores` 字段会汇报当前环境下三台评测核的就绪状态，并在 `catalog_writers` 中列出允许向因子目录（Catalog）写入候选记录的入口。

---

## 1. 三台评测核与两套数据账本

FinAlpha 内置三台评测核，严格区分**可对外引用的点时（Point-in-Time）截面回测**与**仅供安装验证/探索的本地回测**：

| 评测核 | `engine` 标识 | 调用入口 | 直接写入 Catalog | 结果是否可引用 (`citable`) | 说明 |
|---|---|---|:---:|:---:|---|
| 点时截面核 | `pit_core` | `finaince bench`、`finaince eval --backend pit`、`finaince sweep` | 否 | **是**（需点时缓存与 `.manifest.json` 完整） | 基于Polars 向量化截面计算，包含历史成分股切换、停牌/一字涨跌停过滤与五分位换手双边扣费 |
| 引擎原生核 | `engine_native` | `finaince eval --dialect repro_polars --backend local`（底层委托 `reproagent` 的 `build_backtest_bundle`） | 否 | **否** | `build_backtest_bundle` 内部自建固定 DataLoader，不支持显式点时动态股票池，结果不可单独作为晋升或外部引用依据 |
| Qlib 评测核 | `qlib` | `finaince eval --dialect qlib` | 否 | **否** | Python 3.12 下默认通过进程内 `qlib_child` 执行算子评测，用于方言验证与兼容回测 |

### 目录写入与晋升隔离规则

- **评测命令只读不写库**：`finaince eval`、`finaince bench` 与 `finaince sweep` 仅计算并输出指标（`sweep` 在开启 `--dedup-catalog` 时只按 `expr_hash` 查重），三台评测核自身的 `writes_catalog` 均为 `false`。
- **唯一合法的候选写入入口**：只有研究主循环 `loop._catalog_candidate` 与沙箱实现命令 `isolate.upsert_isolated`（`finaince impl`）会将候选因子写入 Catalog（初始状态为 `candidate`）。
- **溯源缺失或不可引用一律拒止**：晋升审查读取 Catalog 记录时，缺少 `engine` 或回测起止窗口的记录会触发 `unprovenanced`；`citable=false` 的记录（包含所有 `engine_native` 与旧库迁移产生的 `engine=unknown` 记录）会触发 `not_citable`。这两项门禁**不可通过 `--override` 豁免**。
- **引用规范**：目录中的历史复现 IC 与 `finaince bench` 基准表上的 IC 属于不同账本。对外引用任何指标时，必须同时注明 `engine`、时间窗口、`cost_bps`、股票池（universe）以及 `data_version`。

---

## 2. 核心工作流职责边界

### 安装探针（`finaince baseline`）
`finaince baseline` 锁死在随包分发的 `local_panel`（两只股票，`2023-01-03` → `2023-02-10`，`0 bps`，`Rank(Delta(close, 1))`）。它的唯一职责是验证本地环境安装完好且计算结果确定可复现，绝不作为研究默认配置。

### 研究主循环与曲线择时（`finaince loop` & `model_head.py`）
- `finaince loop` 在未提供 `--expression` 且不存在筛选产物（`sweep_*.json`）时，直接返回 `expression_queue_empty`，不会静默回退去刷默认公式。
- 循环的第二步（`model_head.train_head`）是**单因子/组合收益曲线的滞后项择时**（采用严格因果的 Walk-Forward 滚动窗口拟合 OLS 或 LightGBM，根据预测值做仓位缩放并扣除换手成本），而非股票截面预测模型训练。

### 动态因子组合（`combination.py`）
`combination.try_combine_ready` 仅接收 `engine=pit_core`、状态为 `ready` 且回测窗口一致的因子成员（窗口不一致时返回 `incompatible_windows`）。权重基于 $t-1$ 日及之前的滚动逆波动率计算，作用在各因子的日收益序列上，并按每日权重向量的 L1 变化率扣除双边交易成本。

---

## 3. 治理门禁与安全机制

### 门禁主路径（`evaluate_gates`）
晋升审核依次执行以下门禁检查：
1. **溯源与可引用性**：`unprovenanced`、`not_citable`（均不可 override）
2. **数据真实性与样本宽度**：`simulated`、`formula_proxy`、`thin_panel`（实际载入面板资产数 $< 20$）
3. **基础统计门槛**：`missing_ic`、`ic_threshold`（$|IC| \le 0.005$）、`missing_returns`、`empty_code`
4. **相关性与多重检验**：
   - `correlated`：与池内已有因子的日收益相关性 $> 0.7$
   - `weak_ic`：Harvey–Liu $t$ 统计量 $|ICIR| \times \sqrt{N_{\text{days}}} < 3.0$（在 `details` 中明确标注使用的是 `rank_icir` 还是 `icir`）
   - `inflated_sharpe`：Bailey–López de Prado 缩减夏普比率 $\text{DSR} < 0.95$（试验次数 $N$ 从审计链中的 `eval` / `isolated_impl` 事件统计）
5. **AST 结构正则化**：
   - `homogeneous`：与种子库或目录已有因子的最大 AST 子树相似度 $> 0.85$
   - `overcomplex`：AST 节点数 $> 40$、自由参数 $> 6$ 或引用特征数 $> 8$

### 对抗重评与沙箱隔离
- **独立子进程对抗重评（`adversary.py`）**：`finaince review --approve` 默认启动干净子进程重新计算该因子的 IC 与 Sharpe，与入库记录偏差超出容差即否决晋升。仅 CLI 调试参数 `--no-adversary` 可跳过；HTTP 接口无此开关，且携带 `override` 请求体固定返回 `403`。
- **双层代码沙箱（`isolate.py`）**：执行用户或 LLM 编写的 `compute(panel)` 代码时，先通过 `ast.NodeVisitor` 白名单拦截属性反射（如 `getattr`、`__subclasses__`）、危险内建函数与非授权模块导入，再优先通过 `bwrap --unshare-all`（无 `--share-net` 网络隔离）或受限冻结内建环境运行子进程。
- **过程记忆与外部裁决**：过程记忆（`process_memory.py`）仅对同时通过门禁与对抗重评的候选赋予正向信用；WorldQuant BRAIN 裁决轨（`brain_track.py`）将平台外部评级回写至 `lineage` 而不篡改本地 IC，无凭据时显式标注 `adjudication_level="internal_dual_window"`；研报语料战役（`corpus_campaign.py`）的 `ready_pit_core` 仅统计 `engine=pit_core` 且可引用的因子。

---

## 4. 外部引擎版本锁定与数据边界

`pyproject.toml` 中的可选依赖通过固定 Git Commit SHA 锁定上游引擎版本，默认关闭本地兄弟目录注入（`FINAINCE_PATH_HACK` 默认关闭）：

| 依赖包 | 锁定 Commit SHA |
|---|---|
| `reproagent` | `18702585ed19f50c85c9dd9b023ebff2a09a56f7` |
| `aiminer` | `99e0924e41cc6c8d70d7670cdf1cf0c63bb3aeaa` |
| `finreportparser` (`finpdfpro`) | `fb5314833a816d40201fb1173a6afe6788dfba2c` |

CSI300 点时行情缓存（`data/`）受版权与体积限制不包含在 Git 仓库中。在第三方使用相同 `data_version` 复核并归档公开基准表之前，历史开发日志（`docs/CHANGELOG.md`）中的本机运行记录不作为公开引用数据。

---

## 5. 历史设计归档

`docs/` 目录下的其余文档保留了早期竞品调研与分阶段设计记录，文中的历史进度表不代表当前验收标准，系统现行行为一律以本文档及源码实现为准：
- [docs/competitor-analysis.md](competitor-analysis.md) — 量化 Agent 竞品机制调研（2026-08）
- [docs/rdagent-gap.md](rdagent-gap.md) — 与微软 RD-Agent 的定位与功能对照（2026-08）
- [docs/improvement-plan-v3.md](improvement-plan-v3.md) — WS-A 至 WS-L 工作流原始设计方案
- [docs/platform-improvement.md](platform-improvement.md) / [docs/platform-improvement-v2.md](platform-improvement-v2.md) — 早期平台迭代方案归档

# FinAlpha 现状（评测核与可引用数字）

| 字段 | 值 |
|------|-----|
| 日期 | 2026-10-12 |
| 地位 | **唯一现状说明**。竞品对比、RD-Agent 差距、v3 改进计划里的「已收口」表以本文为准 |
| 包 | `finaince` 0.1.0，产品名 FinAlpha |

`finaince doctor` 的 `evaluation_cores` 段报告下面三台核此刻是否就绪，并用 `catalog_writers` 列出 catalog 写入方。三台核的 `writes_catalog` 都是 false。缺 pit 缓存不会让 `ok` 变 false。

## 三台核，两套账

| 核 | `engine` | 入口 | 写入 catalog | 可引用 |
|---|---|---|---|---|
| 点时截面 | `pit_core` | `finaince bench`、`finaince sweep` | 否 | 缓存与 manifest 都在时，bench 表可以引用；sweep 仍是筛选，不是晋升依据 |
| 引擎回测 | `engine_native` | `finaince eval --dialect repro_polars` → `reproagent` `build_backtest_bundle` | 否。写入方是 `loop._catalog_candidate` 和 `isolate.upsert_isolated` | 否。bundle 自己建 DataLoader，吃不了显式点时股票池 |
| Qlib | `qlib` | `finaince eval --dialect qlib` | 否 | 否。3.12 上默认走进程内 `qlib_child` |

晋升读 catalog 里的记录。缺 `engine` 或窗口的行失败名为 `unprovenanced`。`citable` 为 false 的行（包括 `engine_native`）失败名为 `not_citable`，不能单独晋升。`finaince eval` 和 `finaince bench` 只打印指标，不写 catalog。catalog 的写入方是 `loop._catalog_candidate` 和 `isolate.upsert_isolated`，状态是 `candidate`。`finaince doctor` 的 `evaluation_cores.catalog_writers` 列出这两个名字，三台核的 `writes_catalog` 都是 false。`sweep` 只在 `dedup_catalog` 打开时按 `expr_hash` 查重，不写入目录。

因此：目录里的 IC 与基准表上的 IC 不是同一笔账。引用数字时带上 `engine`、窗口、`cost_bps`、宇宙和 `data_version`。

## 安装探针，不是研究默认值

`finaince baseline` 锁在包装内的 `local_panel`：两只股票，2023-01-03 → 2023-02-10，成本 0 bps，表达式 `Rank(Delta(close, 1))`。这些数字只证明安装能复现。

`finaince baseline` 仍是上面的安装探针。`finaince loop` 在没有筛选产物、也没有 `--expression` 时返回 `expression_queue_empty`，不会去评测 `Rank(Delta(close, 1))`。有产物时，因子步经 `loop._catalog_candidate` 把评测结果写入 catalog，状态 `candidate`。

`model_head.train_head` 和 loop 的第二步是曲线择时：拟合一条组合收益曲线的滞后项。CLI 帮助用 curve timing 描述它。这不是截面模型研发。

`combination.try_combine_ready` 只接受 `engine=pit_core` 且窗口一致的 ready 成员。窗口不一致时原因是 `incompatible_windows`。权重作用在收益序列上，不是股票持仓。

可引用评测走 `finaince eval --backend pit` 和 `finaince bench`。两者调用 `finaince.eval.pit_core`。`--backend local` 仍是 `engine_native`，`citable` 为 false，不能单独作为晋升依据。

## 治理

已在 `evaluate_gates` 主路径上：`unprovenanced`（缺 `engine` 或窗口）、`not_citable`、`thin_panel`、`formula_proxy`、空 IC、IC 阈值、收益相关、空代码、`weak_ic`、`inflated_sharpe`、`homogeneous`、`overcomplex`。`unprovenanced` 和 `not_citable` 不能被 override 跳过。`weak_ic` 的 detail 写明用的是 `rank_icir` 还是 `icir`。HTTP 请求体带 `override` 固定 403。

`approve` 默认跑对抗重评。只有 CLI `--no-adversary` 可以跳过。HTTP 没有这个开关。

旧 catalog 行在打开库时写成 `engine=unknown`、`citable=false`，状态不变，不会被自动批准。

`isolate.py` 的 bwrap 命令是 `bwrap --unshare-all`，不带 `--share-net`。冻结导入表拒绝子进程里的 `socket`。没有表达式、或评测核没有打出分的 `compute(panel)` 不进 catalog。隔离失败仍写一条 trace。

过程记忆只有同时过了门禁和对抗重评才加信用。BRAIN 回写进 lineage，不改本地 `ic`。无凭据时 `adjudication_level` 是 `internal_dual_window`。战役的 `ready_pit_core` 只数 `engine=pit_core` 且可引用的 ready 行，可以为 0。`no_factors` 不产生 ready 行。

换手模型名是 `quintile_membership_churn`。请求行业中性但面板没有 `industry` 列时，中性化记为 `skipped_no_industry`，IC 不标成已中性化。成分股按每年 6 月和 12 月的第二个星期五切换；当天停牌名单从可交易集合里去掉。

工作台：`FINAINCE_PACKAGED_SPA=1` 使用仓库里的 `src/finaince/web/index.html`。目录行显示 engine、窗口、成本、`citable`。不可引用的行折叠。批准按钮在对抗重评通过前不可用。任务区显示 status、cancel、failure reason、trace id。

## 引擎钉死的版本

`pyproject.toml` extras（升级引擎另开变更，不跟文档勘误绑在一起）：

| 包 | commit |
|---|---|
| reproagent | `18702585ed19f50c85c9dd9b023ebff2a09a56f7` |
| aiminer | `99e0924e41cc6c8d70d7670cdf1cf0c63bb3aeaa` |
| finpdfpro (`finreportparser`) | `fb5314833a816d40201fb1173a6afe6788dfba2c` |

`FINAINCE_PATH_HACK` 默认关。公开安装走上面的 git 依赖，不走作者机器上的兄弟目录。

## 仓库里没有的东西

点时行情缓存不在 git 里（`data/` 被忽略）。`docs/CHANGELOG.md` 记录过一次本机 `finaince bench` 的种子因子数字。那次运行的 parquet 与 JSON 不在本仓库中，第三方不能凭克隆结果复核那张表。在 `docs/bench/` 出现可复现的表之前，README 不把那些格子当作公开引用数字。

## 历史文档

下面几份保留作设计记录。其中的「已收口」和「超越」里程碑不是当前验收状态。对方论文里的收益数字和平台评级率只出现在那些历史文档里，不是本仓库的测量：

- `docs/competitor-analysis.md`
- `docs/rdagent-gap.md`
- `docs/improvement-plan-v3.md`
- `docs/platform-improvement.md`
- `docs/platform-improvement-v2.md`

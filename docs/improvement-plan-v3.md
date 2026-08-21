# FinAlpha 改进优化计划 v3（2026-09）

| 字段 | 值 |
|------|-----|
| 日期 | 2026-08-21 |
| 依据 | `docs/competitor-analysis.md`（11 家竞品深挖）+ 四路代码现状勘察（file:line 锚点）+ 沙箱/RQ 外部实践调研 |
| 范围 | 七个工作流（WS-A…WS-G），每项含：目标 / 现状锚点 / 设计 / 接口契约 / 测试 / 验收 / 工作量 / 依赖 |
| 原则 | 不重写引擎；一切新能力 fail-closed、诚实降级；默认行为零破坏；hermetic CI 不碰网络 |

优先级排序：**A（生成端正则化）> C（吞吐快赢）> D（真宇宙数字）> B（经验链）> E（沙箱）> F（工作台）**，G 持续。

---

## 审查修订（v3.1）：从「追平」到「超越」

> 审查背景：业主要求超越全部前沿竞品，而非追平。独立复审结论：**初版 v3 是追平方案**——WS-A/B 均为采纳竞品已发表机制，做到即打平；WS-D 只是获得参赛资格。（计划请 Oracle 二审，因模型后端许可故障不可用；以下为基于四路勘察与可行性锚点的自审结论，锚点均经源码核实。）

### R1. 超越的定义：可证伪的量化目标（钉死竞品数字）

| 对手 | 其已验证水位 | 超越判据（我方须在同一口径下超过） |
|---|---|---|
| AlphaAgent (KDD'25) | CSI500 2021–2024 含成本 IC 0.0212 / IR 1.49 | 治理内因子环 OOS IR > 1.49 且 IC > 0.0212 |
| AlphaForge (AAAI'25) | CSI300 IC 4.40% | 动态组合上线后 CSI300 IC > 4.40% |
| RD-Agent(Q) | ~2× ARR 于基准库，<$10 成本 | 每 $1 LLM 成本的 OOS Sharpe 高于其可比换算；且全部产物带审计链 |
| AgonAlpha | BRAIN 提交 SPECTACULAR 率 28%（17/60） | SPECTACULAR 率 > 28%，且每次提交附完整治理审计链（他们没有） |
| FAMA (ACL'24) | RankIC 超 SOTA +0.006 | 同基准 RankIC 增量 > +0.006 |

没有 WS-D 的真数据，这些判据一个都无法检验——**故 WS-D 从第三优先提升为第一优先**。

### R2. 结构性杠杆：竞品无法复制或不屑复制的资产

1. **治理栈**（fail-closed 门禁 + audit 哈希链 + 确定性对抗评审）：RD-Agent/AlphaAgent 零治理自动采纳；AgonAlpha 有平台裁决但无本地治理。
2. **中文卖方研报保真管线**（finpdfpro 版面/公式 + 偏差自愈）：X2Strategy/FactorEngine 远不及；这是**独家数据源**。
3. **确定性引擎纪律**（LLM 永不产数字）：所有竞品的评测都或多或少被作者控制的管线污染（AgonAlpha 论文自己指出这一点）；我们的 adversary 重评是确定性的。
4. **trace hypothesis+cites 溯源原语**：比各家的日志更结构化，是过程记忆的最佳原料。

### R3. 新增五个超越型工作流（并入本计划）

#### WS-H 治理内代码因子进化（对 RD-Agent 的正面超越点）
- **无人拥有的组合**：代码级因子进化（CoSTEER 类）× 机构治理。RD-Agent 有进化无治理；AgonAlpha 有裁决无进化深度。
- 设计：LLM 写完整 `compute(panel)` → 冻结沙箱执行（`isolate.py` 已支持 `python_sandbox` 方言，FROZEN_MODULES 含 numpy/polars，已核实）→ 全部门禁 + 对抗评审 → 失败按 error 前缀 + AST-diff 检索改写下一稿（CoSTEER 轻量版）。bwrap 层（WS-E）先行或伴行。
- 判据：同一批假设下，代码因子的 OOS IR 显著高于表达式因子（证明表达力增益未被过拟合吃掉），且 100% 产物可审计。

#### WS-I 组合目标环 + 动态组合（对 AlphaForge 的超越点）
- bandit 奖励从 `portfolio_return` 符号升级为**净换手感知的组合目标**（net Sharpe / IR）；loop 的 model 步升级为跨 catalog ready 因子的**每日动态权重组合**（AlphaForge 式抗衰减），proxy/薄面板行自动降权进入约束。
- 数据条件已核实：catalog `list()` 返回含 daily_returns 的 FactorRecord。
- 判据：动态组合 IC > 单因子最好者（AlphaForge 已证明该形态可达 4.40%）；组合换手受成本约束净值为正。

#### WS-J BRAIN 外部裁决轨（对 AgonAlpha 的超越点）
- 接 worldquant-brain-mcp 模式：FinAlpha 治理流（提案→门禁→对抗→approve）的产出经 API 提交 BRAIN，平台评级回写 catalog lineage。
- 论点：AgonAlpha = 平台裁决无治理；FinAlpha = **平台裁决 × 治理原生**，两者兼有的第一个开源台。
- 失败模式诚实降级：无账号/名额时回落「内部双窗基准表」（WS-D），并在输出中声明裁决等级下降。
- 判据：SPECTACULAR 率 > 28%；每次提交的 prompt-to-expression 溯源 + 本地审计链同时公开。

#### WS-K 研报语料战役（独家数据护城河）
- 批量过夜复现 categorized 知识库数百份券商研报（JobRunner 队列 + 断点续跑 + `no_factors` 诚实记账），产出**中国卖方因子库**——任何竞品都没有这条数据管道。
- 每份产出走完整治理流；语料级统计（抽取率/偏差通过率/机制族分布）成为可发表副产品。
- 判据：≥100 份研报完成治理内复现，其中 ≥30 个因子进入 ready 且通过 WS-D 双窗检验。

#### WS-L 治理接地过程记忆（对 AlphaMemo 的超越点）
- AlphaMemo 用原始残差做信用分配；我们用**「是否存活门禁+对抗评审」作为更强的结果信号**，对 AST-diff 编辑动机做置信加权记忆——治理事件本身就是高质量标签。
- 吸收 WS-B 经验链为其展示层；非对称否决语义与我们既有 fail-closed 纪律同构。

### R4. 重排后的路线

```text
第 1 周   WS-D 数字轨（一切判据的前提）＋ WS-C 快赢 ＋ WS-A 正则化
第 2 周   WS-E bwrap（WS-H 的安全前提）→ WS-H 治理内代码进化
          WS-I 组合目标环
第 3–4 周 WS-K 语料战役启动（夜间队列）   WS-J BRAIN 轨（凭据就绪后）
          WS-L 过程记忆    WS-B/F/G 穿插
里程碑    M1*: CSI300 双窗基准表发布（D）——判据标定
          M2*: 代码因子 OOS IR > 表达式因子（H）
          M3*: 动态组合 IC > 4.40%（I）
          M4*: BRAIN SPECTACULAR 率 > 28% 且全链审计公开（J）
          M5*: ≥100 份研报复现台账（K）
```

### R5. 初版 WS-A…G 的处置

| 项 | 处置 |
|---|---|
| WS-A 正则化 | 保留，第 1 周（生成时正则化是一切搜索的质量底座） |
| WS-B 经验链 | 并入 WS-L（链降级为展示层，信用分配升级为治理加权） |
| WS-C 吞吐快赢 | 保留，第 1 周 |
| WS-D 数字轨 | **升为最高优先**（R1 判据的前提） |
| WS-E bwrap | 保留并提前（WS-H 安全前提） |
| WS-F 工作台 | 保留，穿插 |
| WS-G 质量 | 持续 |

### R6. 风险与诚实边界

- WS-J 依赖 BRAIN 账号/名额/平台规则变动；降级路径已在设计内。
- WS-K 依赖 PDF 语料规模与 LLM 抽取质量；`no_factors` 是合法终态，禁止凑数。
- 全部量化判据依赖 WS-D；在 M1* 之前，任何「超越」表述都是未检验主张——对外一律不得声称。
- Oracle 二审因基础设施故障缺席；本修订为单引擎自审，M1* 后应以真实数字代替观点复核。

---

## WS-A 生成端正则化：AST 原创性 + 复杂度惩罚（AlphaAgent 式）

**动机**：LLM 挖因子的头号衰减源是同质化；AlphaAgent 消融显示三正则化把 hit ratio 从 0.16 提到 0.29（+81%）。

### 现状锚点
- 算子表：`eval/operators.yaml`（17 算子 + 别名，arity 齐全）；`router.listed_operators()`（router.py:36-58）已缓存解析。
- 校验是**正则级**：`is_listed()`（router.py:61-66）只查 `Word(` 是否在 YAML；无任何 AST 解析。
- 门禁框架：`gates.evaluate_gates()`（gates.py:100-204）；override 语义在 line 107（`skipped = set(override)`）；新门禁插槽 ~line 175。
- catalog **无结构哈希**：store.py 存 `formula` 原文 + `record_json`，无 `expr_hash`，无 `normalize_expr`。
- 生成时钩子：`discovery.cull_factor_pool()`（discovery.py:23，委托 `aiminer.manager.cull_alpha_pool`）是候选入池前唯一汇聚点。
- 相似度现状：只有收益序列相关（gates.py:148），无表达式结构比较。
- Alpha101 语料：**仓内不存在**，需自建种子库。

### 设计
1. 新模块 `src/finaince/expr_ast.py`：
   - `parse(text, dialect) -> OpTree`：两种方言都是函数调用形，剥 `$` 后可 `ast.parse`；产出统一算子树（节点 = 算子名+参数桶+子树）。
   - 规范化：交换算子（Corr/Mul/Max/Min/Add 类）子树按序列化形式排序；窗口等数值参数进粗分桶（如 5/10/20/60）；一元包装折叠。
   - `similarity(tree_a, tree_b) -> float`：最大同构子树节点数占比（AlphaAgent 式 subtree-isomorphism，用子树哈希加速）。
   - `complexity(tree) -> {sl, pc, fc}`：符号长度（节点数）、自由参数个数（数值字面量）、特征数（去重字段引用）。
2. 种子库 `tests/fixtures/alpha_seed_zoo.json`：手工整理 ~30 条白名单可表达的经典因子（动量/反转/波动/量价），作为对照基线；catalog 本身随时间自然成长为第二对照库。
3. 门禁两个（沿用 override 语义，可跳过名单加 `homogeneous`/`overcomplex`）：
   - `homogeneous`：与 catalog 任一 ready/candidate 行或种子库的最大相似度 > 0.85 → fail（对齐 AgonAlpha 自相关门前置哲学）。
   - `overcomplex`：`sl > 40 或 pc > 6 或 fc > 8`（阈值先宽后紧）→ fail。
4. 生成时过滤：`cull_factor_pool()` 入口先跑结构去重（同义候选直接淘汰并记 reason），demo/swarm/cull-json 三条路都过这里，天然全覆盖。
5. catalog 增强：upsert 时写 `expr_hash`（规范化树哈希）列（ALTER 模式同 jobs 表），O(1) 查重。

### 接口契约
```python
# expr_ast.py
def parse(text: str, dialect: str) -> OpTree            # 解析失败 raise ValueError
def similarity(a: OpTree, b: OpTree) -> float           # [0,1]
def complexity(tree: OpTree) -> dict                    # {"sl":int,"pc":int,"fc":int}
def max_similarity_vs(text: str, dialect: str, corpus: list[tuple[str, str]]) -> float
# gates.py 新增
def homogeneous_gate(record, corpus) -> GateResult      # detail 带 top 相似对象 id
def overcomplex_gate(record) -> GateResult
```

### 测试 / 验收
- 单测：解析（两方言 × 边界）、规范化幂等、相似度序（自身=1，换窗口参数 <1，无关 ≈0）、复杂度计数。
- 门禁：完全重复被 `homogeneous` 拦截；`Rank(Delta(close,1))` vs `Rank(Delta(close,2))` 相似度落在 (0.5,1) 区间；8 层嵌套 5 参数的表达式被 `overcomplex` 拦截；override 放行路径。
- 集成：`discover --demo` 后重复候选不再二次入池。
- **验收信号**：同一批候选里近重复表达式 100% 被结构去重淘汰（当前 0%）。

工作量 **M**（2–3 天）。依赖：无。这是全计划性价比最高的一项。

---

## WS-C 吞吐与调度：panel 缓存 + sign-reflection + 并发守卫

### 现状锚点
- **每次 evaluate 都重读 parquet**：`run_factor_step` → `evaluate` → `build_backtest_bundle`（router.py:171），无内存缓存；`_PANEL_STATS_CACHE`（runtime.py:325）只缓存统计不缓存数据。
- JobRunner **无并发模型**：submit 内联或 detached 子进程（runner.py:158/209）；`max_concurrent=2` 只是 fallback API 的契约数字（aiminer_fallback.py:463/884），**无任何强制**。
- **pending-aware 钩子不存在**：无法查询「同 panel 是否已有在途 eval」。
- **sign-reflection 不存在**：grep orient/flip/negative 仅命中注释（loop.py:11）。
- 批量环已有：`run_loop(expressions=[…])` 但串行逐条。

### 设计
1. **Panel 进程级缓存**（eval/router.py）：`_PANEL_CACHE: dict[(path, mtime_ns, size), pl.DataFrame]`，键复用 `_PANEL_STATS_CACHE` 模式；`build_backtest_bundle` 前挂 load 钩子（reproagent 侧不可改则用 monkeypatch 式注入或在 finaince 侧预读传参——以 spike 结论为准）。批量环内 300 表达式共享一次读盘。
2. **sign-reflection**（AgonAlpha 式）：新函数 `reflect_sign(metrics) -> metrics`——美元中性截面因子取反后 IC/ICIR/Sharpe 精确变号、turnover/回撤幅值不变；接入 `cull_factor_pool`：负 IC 且 |t|≥3 的候选生成镜像行一并评分，省一半模拟预算。
3. **并发守卫**：`runner.can_submit(kind, panel_key)` 扫描 running/queued 行的 payload；`run_loop_job(async)` 提交前检查，冲突时返回 `{"ok": false, "error": "duplicate_pending", "running_job_id": …}`；同时给 loop/swarm 子进程加真实信号量（env `FINAINCE_MAX_JOBS`，默认 2，让契约数字变成强制）。
4. **批内并行**（可选后置）：`run_loop(expressions, workers=N)` 用 ProcessPoolExecutor 分片表达式，panel 由各 worker 各自加载一次（跨进程缓存不共享，收益主要在 CPU 型指标计算）。

### 测试 / 验收
- 缓存：mtime 变化失效；批量 50 表达式只触发 1 次读盘（monkeypatch 计数）。
- sign-reflection：手工构造负 IC 因子，断言镜像行 ic=-ic、|sharpe| 相等、turnover 相等。
- 守卫：同 panel 第二个 async job 被拒；FINAINCE_MAX_JOBS=1 时第二个 submit 排队/拒绝语义明确。
- **验收信号**：50 表达式批量墙钟时间 ≥3× 缩短（缓存+并行）；重复提交 0 尝试。

工作量 **M**（缓存+守卫 1–2 天；sign-reflection 1 天；批内并行可选 +1 天）。依赖：无。

---

## WS-D 真宇宙数字轨：CSI300 长窗可引用基准

### 现状锚点
- universe 是**纯标签**：`_CSI300_CLAIMS`（gates.py:10-24）只用于薄面板声称检测；无成分股逻辑。
- RQ 抓取全在外部 reproagent 包内；finaince 只传 `settings(data_source="ricequant")` + universe 字符串（router.py:145-148/163-169）。凭据缺失时错误以 `backtest_failed:{exc}` 冒泡（router.py:172-182），无前置检查。
- 默认窗硬编码 `2024-01-02→03-29`（runtime.py:26-27）；snapshot 金标只有 3 条表达式（snapshot.py:28-61）；**无基准表结构**。
- CI 边界：offline workflow 无凭据全绿（pytest-offline.yml:51-58），live 全部 `-m live` 隔离。

### 设计（外部实践已调研）
1. 新模块 `src/finaince/data_track.py`：
   - 成分股 **point-in-time**：`rq.index_components("000300.XSHG", start_date, end_date)` 按日取历史成分，取并集构建 survivorship-free 宇宙；`rq.get_suspended_stocks(date)` 逐日剔除停牌；权重用 `index_weights(date)` 注意 T-1 语义（调仓日 = 每年 6/12 月第二个星期五，中证官网规则）。
   - 年度分块抓取（`get_price(frequency="1d", adjust_type="pre")`），Int64→float64 coerce 复用既有 `_pandas_to_polars` 经验。
2. 缓存布局（qlib/vectorbt 惯例 + sha256 完整性）：
   ```text
   $FINAINCE_HOME/data_track/csi300/v1/
     ├── {year}/csi300_{year}.parquet + .sha256
     ├── constituents/components_{date}.parquet
     └── .manifest.json   # schema_version, index_code, last_updated, fields
   ```
   读时校验哈希；`trade_date` 存 bar 日本身防前视。
3. `finaince bench` 命令 + `GET /api/v1/bench`：
   - 锁定窗：IS 2019-01-01→2023-12-31，OOS 2024-01-01→2024-12-31（env 可覆盖）；cost_bps 默认 5（双边）；universe=csi300(point-in-time)。
   - 输出基准表 JSON+Markdown：N 条种子表达式 × {IC, RankIC, ICIR, AR, IR, Sharpe_net, turnover} × {IS, OOS}，附 Alpha158-lite 对照行（rank-delta / 反转 / 量价冲击三类基线因子）。
   - 所有数字带 `{window, cost_bps, universe_source: "point_in_time", data_version}` 溯源块——延续 baseline 的 claim 纪律。
4. doctor 增加 `data_track` 就绪段（缓存版本/最新年份/成分日期覆盖）。
5. 与 reproagent bundle 的衔接需 **spike**：确认 build_backtest_bundle 能否吃显式股票池列表；不能则在 data_track 内实现独立轻回测（polars 向量化 IC/分层），不走 bundle。

### 测试 / 验收
- hermetic：假 parquet + 假 constituents 走完整 bench 管线（无网络）；manifest/哈希校验；point-in-time 对齐单测（给定调仓日断言成分切换）。
- live（`-m live`）：真实抓一年 CSI300 → bench 出表。
- **验收信号**：产出第一张含成本、point-in-time、双窗的可引用基准表；所有数字可由第三方用 manifest 复现。

工作量 **L**（3–5 天，spike 先行 0.5 天）。依赖：米筐凭据（用户侧）。

---

## WS-B 经验链结构化（FAMA CoE）

### 现状锚点
- `coaching.research_context` 已把样例+教训散点注入 advisor prompt（loop.py:101-119）与 SDK 工具（tools.py:181-191）；无链式结构。
- trace 已有 `hypothesis` 列与 cites 链（trace.py），是链的原料但无「簇-链」组织。

### 设计
1. 链存储：`$FINAINCE_HOME/coaching/chains.json`（versioned；不进 platform.db，避免迁移）。
2. 结构：按收益相关性 KMeans（k≤8）聚簇；每簇一条链 = 有序因子 id 序列；新因子与链上相关最高处若为链尾→扩展，否则从匹配点分裂新链（FAMA Eq.15 规则）；仅当新因子 RankIC 超链上全部才入链。
3. `research_context` 增 `chains` 字段：advisor prompt 引用「链位置 + 上一跳编辑摘要」，SDK 工具同步透出。
4. 与 WS-A 联动：链上相邻因子对自动积累 AST-diff 编辑动机频次（AlphaMemo 简化版，只做计数不做残差学习），作为 advisor 的「该簇常用变换」提示。

### 测试 / 验收
- 链规则单测（扩展 vs 分裂 vs 拒绝）；持久化 round-trip；advisor prompt 含链上下文。
- **验收信号**：连续 20 步 loop 后 chains.json 形成非空簇结构，advisor prompt 可见链位置。

工作量 **M**（2 天）。依赖：WS-A（AST-diff 动机部分）。

---

## WS-E 沙箱加固：bwrap 可选层

### 现状锚点
- `isolate.py` 当前是冻结内建 + AST 危险模式检查的同进程 exec；无 OS 级隔离。

### 设计（外部对比结论：bwrap 冷启动 <5ms、rootless、Claude Code/Flatpak 同款）
1. 两层可选升级，fail-closed 回落：
   - L1（现有）：frozen builtins。
   - L2（检测到 bwrap）：`bwrap --unshare-all --share-net --ro-bind <venv> <venv> --tmpfs /tmp --proc /proc --dev /dev --new-session --die-with-parent -- <python> -m finaince.isolate`；`--new-session` 防 TIOCSTI（CVE-2017-5226）；seccomp BPF 后置（先裸跑稳）。
   - 环境变量 `FINAINCE_SANDBOX=bwrap|auto|off`（默认 auto=可用即用）。
2. doctor 报告 `sandbox_backend`；isolate 结果 dict 增 `via` 字段诚实标注实际层。
3. Docker-in-Docker 场景 `--unshare-all` 可能失败——探测失败自动回落 L1 并 warning，不硬崩。

### 测试 / 验收
- 无 bwrap 环境：行为与今天一致（CI 保证）。
- 有 bwrap 环境（本地手测）：恶意样例（socket/subprocess/写盘）被 namespace 拦截；正常 compute(panel) 结果与 L1 一致。
- **验收信号**：doctor 如实报告 sandbox_backend；两层结果一致性抽检通过。

工作量 **S-M**（1–1.5 天）。依赖：无。

---

## WS-F 工作台呈现

### 现状锚点
- wheel 内 `web/index.html` 是 stub（一行 div）；完整 SPA 在 aiminer 仓。
- aiminer_fallback 已提供全套 `/api/*`（swarm/results/backtest/wiki/charts/ws）；**成本/adversary/trace 数据前端今天就能拿到**（serve.py:276/321 + backtest metrics）。
- 缺口：门禁失败没有独立只读端点（只在 approve 响应里）。

### 设计
1. 新端点 `GET /api/v1/review/{promotion_id}/gates`：只读运行 `evaluate_gates` 返回 failures/details/override 名单（不改状态）。
2. stub SPA 最小升级（纯静态、零构建链）：单页三栏——Catalog 列表（含 cost/sharpe_net/proxy 标签）、Review 队列（gates 报告 + adversary 按钮）、Trace 链（hypothesis 时间线）。数据源全部用既有 `/api/v1`。
3. aiminer 前端仓的对应页面增强列为独立事项（不在本仓范围，记录到 rdagent-gap A' 项）。

### 测试 / 验收
- TestClient 断言新端点与 stub 页面关键 DOM/路由字符串（沿 test_frontend_helpers.py 模式）。
- **验收信号**：不开 aiminer 前端也能在浏览器完成「看门禁 → 跑对抗 → 看 trace」闭环。

工作量 **S-M**（1–2 天）。依赖：无。

---

## WS-G 质量与交付（持续）

- 补零覆盖面：`eval/dialects.py`（is_listed/attach_translation 无任何测试）、`run_loop` 真 eval e2e、runner 并发语义。
- `ruff` 进 `[dev]` extra + offline CI 一步（lint-only，不阻塞存量告警）。
- 文档纪律延续：每个 merged PR 同步 handbook §5-§8（本轮审计已建立基线）。
- 竞品雷达：每季度重跑 competitor-analysis 的检索矩阵，增量更新。

---

## 排期与依赖图

```text
第 1 周   WS-A 正则化 ──────────► WS-B 经验链（用 AST-diff）
          WS-C 快赢（缓存+守卫+sign-reflection）
第 2 周   WS-D 数字轨（spike → 抓取 → bench）      ← 需米筐凭据
          WS-E bwrap 层        WS-F 工作台
持续      WS-G
里程碑    M1: 近重复候选 100% 结构拦截（A）
          M2: 批量墙钟 ≥3× 提升（C）
          M3: 第一张可引用 CSI300 双窗基准表（D）
```

## 明确不做（延续冻结项）

- 不做交易信号域 / 个股研报域；不让 LLM 产数字；不追 vaporware。
- 不合并两套 Polars 引擎；不动 rustminer 只读约束；不把 platform.db 迁 SQLModel。
- 不引入 Celery/K8s；调度上限就是单机 JobRunner + 信号量。

# FinAlpha 与微软亚研 RD-Agent 的差距分析与改进方案

| 字段 | 值 |
|------|-----|
| 日期 | 2026-08-14 |
| 对照对象 | Microsoft Research Asia **RD-Agent**（`microsoft/RD-Agent`，NeurIPS 2025 *R&D-Agent-Quant*） |
| 本侧 | **FinAlpha**（包名 `finaince`）+ 引擎 `aiminer` / `reproagent` |
| 原则 | 对照公开代码与论文场景，不拿宣传页上的「卫星/舆情/物流」当量化主链；本侧以仓库现状为准，不以过期规划文档为准 |

---

## 1. 先定性：不是同一条赛道上的落后版

RD-Agent 是**通用数据驱动研发框架**，量化只是其中一个 scenario。它的方法论是固定的两环：

- **R-Loop**：根据 trace（假设 → 实验 → 反馈）提出下一步做因子还是做模型。
- **D-Loop**：把假设写成可执行物（Docker 里跑的 Python / Qlib yaml），用 CoSTEER 一类进化策略根据报错和相似失败知识改代码，直到过评或耗尽试次。

量化入口是现成的：

| 命令 | 做什么 |
|------|--------|
| `rdagent fin_quant` | 因子–模型联合进化（bandit / LLM / random 选下一步） |
| `rdagent fin_factor` | 只进化因子 |
| `rdagent fin_model` | 只进化预测模型（默认 Qlib `GeneralPTNN` / LSTM / GRU） |
| 研报抽取入口 | LangChain 读 PDF + 首页截图 → 抽因子任务 → 再进 factor RD loop |

默认战场是 **Qlib 本地 `cn_data`、CSI300、2008–2020 切分、TopkDropout 组合、Docker 执行**。论文数字（约 2× ARR、少 70% 因子、成本 &lt; $10）绑定这条栈，不是桌面工作台指标。

FinAlpha 是**中国卖方研究台**：人打开 `finaince serve`，用 catalog / review / reproduce / swarm，把「发现」和「研报复现」收成一份可复核的目录。它不追求在 MLE-bench 上当 ML 工程师，也不把预测模型架构搜索当作主产出。

所以「差距」里有三类，不要混着打分：

1. **真差距**：RD-Agent 有、且对量化研究有用、本侧缺的闭环。
2. **定位差**：他们做科学家循环，我们做人机复核台——不是落后，是产品选择。
3. **我们更强或不该学**：中文研报保真、fail-closed 晋升、同源工作台、诚实失败。

旧笔记 `aiminer/docs/notes/rd_agent_gap.md` 把 RD-Agent 写成「卫星 + GNN + 知识图谱」。量化主链源码里**没有**这些；模型侧还明确写了 *Do not generate GNN model as for now*。下面以仓库为准。

---

## 2. 两边现在实际有什么

### 2.1 RD-Agent（公开主链）

- **假设对象**：`Hypothesis` + `Trace.hist`，每步记下 action（`factor` | `model`）、观察、辩护、沉淀知识。
- **动作选择**：`EnvController` bandit，或 LLM 读整段 trace，或随机。因子侧前几轮被 prompt 推向「先试简单快的」，之后推向「高 IC、避开 SOTA 库相似因子」。
- **实现**：`factor_coder` / `model_coder` 在 Docker 工作区写完整 Python 与 Qlib 配置，不是一行表达式。失败进 CoSTEER：摘要错误、查相似失败、再改。
- **评测**：Qlib 信号分析 + 组合回测（IC / 收益 / 回撤那一套），反馈写回 trace。
- **研报**：`load_and_process_pdfs_by_langchain` + 首页截图，产出 factor experiment 再实现。不是版面/公式引擎。
- **运行时**：Linux + Docker + conda 3.10/3.11，`pip install rdagent`，LiteLLM 多模型。
- **知识**：coder 知识库（成功/失败实现检索）+ 实验 trace，不是研究员 wiki。

### 2.2 FinAlpha（仓库现状）

- **发现**：aiminer Manager–SubAgent swarm；角色写 **Qlib 算子白名单表达式**；`selection_score` + IC/相关 `cull_alpha_pool`；3.12 平台通常 **subprocess 到 3.10 conda**。
- **复现**：`reproagent` 摄入 → `finpdfpro` 版面/公式 → polars 回测 → 偏差自愈 → 库；`no_factors` 是合法终态。
- **平台壳**：`FactorRecord` catalog、`(dialect, backend)` eval、`promote → review → approve` fail-closed（`thin_panel` / `formula_proxy` / 空 IC / 空收益 / 相关）、JobRunner、同源 `/` + `/api/v1`、`finaince doctor`。
- **Agent**：Claude Agent SDK + 进程内工具 + 发现/复现/复核三个 specialist。**一次 query，不是过夜 R/D 环。**
- **知识**：chroma RAG + wiki vault + reproagent `report_knowledge` / feedback。没有「上一步失败 → 下一步改因子还是改模型」的因果树。
- **qlib**：3.12 上 `POST /api/v1/eval dialect=qlib` **诚实 `ok: false`**（占位或子进程）。不是 CSI300 上的活 AlphaEval。
- **工作台**：Catalog / Review / Reproduce / Agent / Swarm / Pool / Manual / Wiki 同源；Refresh 深链仍出壳。

---

## 3. 对照表

| 维度 | RD-Agent (Q) | FinAlpha | 判定 |
|------|----------------|----------|------|
| 产品形态 | 无人值守 R&D 工厂，CLI scenario | 研究员工作台 + 引擎调度 | 定位差 |
| 研究环 | Trace 上 R 提议、D 实现、反馈回写 | 人点按钮 / 一次 agent turn | **真差距** |
| 因子形态 | Docker 里的 Python + Qlib 数据模板 | 白名单表达式 + polars 方言 | **真差距**（深度）；本侧更可控 |
| 模型研发 | LSTM/GRU/PTNN、超参、与因子交替 | 无模型架构搜索；有组合模板/手工回测 | **真差距** |
| 因子–模型协同 | bandit/LLM 选下一步 | 两条线靠 catalog 汇合，无联合目标 | **真差距** |
| 研报 | LangChain PDF + 截图 → 再实现 | finpdfpro 版面/公式 → 回测 → 偏差 | **本侧更深**（中文卖方） |
| 实现进化 | CoSTEER：错因摘要 + 相似失败 | 复现有偏差自愈；swarm 靠下一轮角色 | 半差距 |
| 评测宇宙 | Qlib CSI300 长窗、组合分析 | 本地薄面板 / 米筐；门禁防伪 CSI300 | 本侧更诚实；他们更接近「论文宇宙」 |
| 知识 | Trace + coder 成败库 | catalog + wiki + memory 表 | 半差距（缺因果链） |
| 人机复核 | 弱；看 UI trace | 晋升门禁、reject、audit | **本侧更强** |
| 交付 | 单包 + Docker | 3.12 壳 + 3.10 swarm 拓扑 | 各有债 |
| 基准数字 | MLE-bench、论文 ARR | 离线 pytest + 固定 PDF live | 他们有对外数字 |

---

## 4. 真差距（值得补的）

### 4.1 没有过夜研究环

RD-Agent 的 `Trace` 是一等公民：每步假设、动作、实验、反馈都挂在同一条历史上，下一步由 bandit 或 LLM **读历史** 决定。

FinAlpha 的 swarm 是「多角色同一代出表达式 + cull」。`finaince agent` 是最多十几轮的工具调用，停了历史就断。没有「这一轮模型换 GRU 是因为上一轮因子库 IC 饱和」这种可复盘决策。

### 4.2 产出停在公式，到不了可训练模型

RD-Agent 的 D 环写的是工作区代码和 Qlib 训练 yaml。FinAlpha 的 FactorAgent 只允许白名单算子。这换来可翻译、可门禁、难幻觉出 `scipy` 黑洞，但也意味着：

- 学不到时序网络、非线性变换、可训练超参；
- 和论文里「因子库 + 预测模型联合进化」不在一个产出层级。

### 4.3 没有因子–模型联合目标

`fin_quant` 用组合回测指标驱动「下一步改因子还是改模型」。FinAlpha 的 discovery 优化 selection/IC，reproduction 对齐研报，review 看门禁。三套目标，没有一个「策略 ARR / 稳健性」把两边拧在一起。

### 4.4 实现失败不会系统性进化

RD-Agent 把编译/形状/数值错误送进知识库，下一轮显式检索相似失败。FinAlpha 复现有偏差自愈，但 swarm 表达式挂了通常只是这一轮死掉。没有「这类 `Ts_Rank` 窗口在薄截面上炸了」的可查询失败档案。

### 4.5 研报之后没有「实现进化」

RD-Agent：抽任务 → coder 在 Docker 里写到能跑。  
FinAlpha：抽公式 → polars 回测 → 对不上就自愈或 `no_factors`。

后者对卖方研报更诚实（抽不出就不装有）。但「公式能讲清楚、引擎跑不起来」时，没有一层 **受控代码补丁**（仍要过 validate + 门禁），只能停。

### 4.6 没有对外可引用的研究基准

RD-Agent 用 CSI300 + 固定切分讲 ARR。FinAlpha 有离线契约测试和三份 pinned PDF 的 live 标记，没有一份「同一宇宙、同一成本、可复现」的策略数字。qlib 在 3.12 上还不能当这条宇宙。

---

## 5. 不要学、或已经强于对方的

| 点 | 为什么留下 |
|----|------------|
| fail-closed 晋升 | RD-Agent 容易把「跑通的实验」当成可上线因子。本侧 `thin_panel` / `formula_proxy` / 空收益 是生产纪律。 |
| 中文研报流水线 | LangChain 切 PDF 对付不了券商双栏、公式、扫描件。`finpdfpro` 是差异化，不要换成通用 loader。 |
| 同源工作台 + doctor | RD-Agent 要 Docker 健康检查；研究员日常路径更短的是 `doctor` → `serve` → 点 Catalog。 |
| 诚实失败 | `no_factors`、qlib `ok: false`、裸 `discover` 拒绝伪装。不要为了对齐论文把占位评测标成成功。 |
| 算子沙箱作默认 | 完全放开「自己 pip install 再写任意 Python」会毁掉可审计性。代码补丁必须是可选、隔离、过门禁的一层。 |
| 泛化成 Kaggle / 微调 LLM | RD-Agent 的广度不是量化台的 KPI。 |

旧笔记里「引入 Data Profiling / 让 FactorAgent 写完整 Python 文件」方向对，但**不能**作为无门禁的默认路径。

---

## 6. 改进方案（对齐有用部分，不重写成 RD-Agent）

总原则：把 R/D 环和 coder 进化**接在 catalog / review / jobs 后面**，不新开第三套引擎，不替换 finpdfpro。

### 阶段 A — 研究记忆变成因果链（4–6 周）

**目标**：一次 swarm 或一次 reproduce 结束后，人能回答「下一步为什么改这个」。

- 给每次 job 写 `TraceEvent`：假设、动作（`discover_expr` | `reproduce` | `eval` | `promote`）、指标摘要、错误、指向 catalog id。
- 工作台 Run 详情展示这条链，而不是只 tail 日志。
- Agent playbook 强制：先读最近 N 条 trace，再决定调用 `discover_swarm` 还是 `reproduce`。
- **验收**：同一 `FINAINCE_HOME` 连跑两轮后，第二条事件能引用第一条的 `job_id` + 指标；单测走真实 `create_app()` / job runner，不重写一条假链。

这是 RD-Agent `Trace.hist` 的最小有用子集，不需要他们的全量 hypothesis 字段。

### 阶段 B — 受控代码因子作为二等公民（6–8 周）

**目标**：表达式不够时，允许 **workspace 里的 Python 因子**，默认仍是白名单表达式。

- 新 `expression.dialect = "python_sandbox"`。实现落在 `FINAINCE_HOME/workspaces/<id>`，**Docker 或 bwrap**，只读本地/米筐面板，超时杀掉。
- 成功产物仍 upsert catalog：有 IC、日收益、`lineage.engine`。晋升走**同一套门禁**。
- 失败写入 trace，供下一轮检索（轻量 CoSTEER：同错误类最近 5 条，不要上完整知识图谱）。
- **不要**让沙箱 `pip install`。库表冻结（numpy/pandas/polars/scipy 白名单）。
- **验收**：一个故意写错轴的因子第一次失败、第二次改对、catalog 出现一行；`thin_panel` 声称 CSI300 时 approve 仍拒绝。

### 阶段 C — 因子–模型联合环（8–10 周，可与 B 部分重叠）

**目标**：`finaince discover --loop` 能交替「补因子」和「调预测头」，优化的是**组合指标**而不只是 IC。

- 预测头先做薄的：线性 / LightGBM / 一个固定 GRU，Qlib 或现有 polars 面板均可，**先锁一个宇宙**（建议：米筐或本地全样本，明确标注不是论文 CSI300）。
- 动作选择先 bandit（因子 vs 模型），LLM 选择放后面。
- SOTA 库 = catalog 里 `status=ready` 且过门禁的行，prompt 里禁止近似重复（已有相关 cull，接到 loop 即可）。
- **验收**：固定种子、固定窗，loop 产出「因子集 + 一个模型配置 + 组合曲线」；数字写入 `output/loop-baseline/` 并提交配置，不提交「打赢 RD-Agent ARR」这种不可复现口号。

### 阶段 D — 研报 → 实现进化，而不是研报 → 停（4 周）

**目标**：finpdfpro 抽出公式但 `repro_polars` 拒译时，走阶段 B 的沙箱补丁，而不是直接 `no_factors`。

- `no_factors` 仍保留：抽取为空就必须诚实。
- 新增 `status=needs_impl`：有自然语言/残缺公式，生成沙箱任务。
- **验收**：广发系列 5 这类真空抽取仍是 `no_factors`；「公式能讲、方言不能跑」的夹具 PDF 能变成 catalog 一行或明确的 `needs_impl` 失败，而不是空成功。

### 阶段 E — 可引用基准（持续）

- 3.10 上用真实 Qlib 子进程跑一组固定表达式，快照进 `tests/fixtures/`（已有 snapshot 方向，补宇宙与切分说明）。
- 公开对照只报：**同数据、同窗、同成本** 的本侧数字。禁止用论文 ARR 当验收。
- doctor 增加：qlib 子进程是否可用、Docker/沙箱是否可用。不可用 = `degraded`，不是假装 loop 在跑。

---

## 7. 建议排期与明确不做

```text
现在 ──A 因果 trace──► 工作台能复盘
        ──B 沙箱因子──► 表达式不够时仍能进 catalog
            ──C 联合环──► 有策略级目标
                ──D 研报补丁──► 抽取与实现断开时还能前进
                    ──E 基准──► 对外只报可复现数字
```

**不做：**

- 重写成 `rdagent` 的 scenario 插件。
- 默认宇宙改成他们的 `~/.qlib/qlib_data/cn_data`（除非单独装一轨「论文对照」）。
- 为对齐而关掉 `thin_panel` / `formula_proxy`。
- 把 Claude desk 换成无人值守过夜进程却不经 JobRunner。
- 做 Kaggle / LLM 微调 / 通用 Data Science Agent。

---

## 8. 一句话策略

RD-Agent 证明了：**假设 → 隔离实现 → 反馈 → 再假设** 能把因子和模型一起推。FinAlpha 已经证明：**中文研报可以诚实复现，因子可以 fail-closed 晋升，研究员可以在一个同源台上做完**。

下一阶段不是「成为微软的量化工厂」，而是把他们的 **Trace + 受控 coder + 联合目标** 接到我们已经会诚实结束的 desk 上。

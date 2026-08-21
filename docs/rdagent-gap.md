# FinAlpha 与微软亚研 RD-Agent 的差距分析与改进方案

| 字段 | 值 |
|------|-----|
| 日期 | 2026-08-14（2026-08-14 晚按仓库补丁修订：A/B/C/D 骨架已进主链） |
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
- **知识**：chroma RAG + wiki vault + reproagent `report_knowledge` / feedback。**另有** `trace_events`：eval / job（reproduce、swarm、impl、loop）追加一条，后一条 `cites` 前一条 id。还不是带 hypothesis 正文的研发树。
- **qlib**：3.12 上 `eval --dialect qlib` 走 in-process `qlib_child`（schema 不对的 `~/Documents/Data` 回退打包 `local_panel`）。`FINAINCE_QLIB_SUBPROCESS=1` 才用 3.10 子进程。doctor / health 报告 `isolator`、`qlib_child.via`。
- **工作台**：Catalog / Review / Reproduce / Agent / Swarm / Pool / Manual / Wiki 同源。
- **已补骨架（2026-08-14）**：`finaince trace` / `GET /api/v1/trace`；`finaince impl` 冻结模块跑 `compute(panel)` 再 upsert catalog（仍过门禁）；`finaince loop` 交替 factor + thin long-short 组合曲线；空抽取 `no_factors`，能描述但译不了 `needs_impl`。

---

## 3. 对照表

| 维度 | RD-Agent (Q) | FinAlpha | 判定 |
|------|----------------|----------|------|
| 产品形态 | 无人值守 R&D 工厂，CLI scenario | 研究员工作台 + 引擎调度 | 定位差 |
| 研究环 | Trace 上 R 提议、D 实现、反馈回写 | 人点按钮 / 一次 agent turn；**已有事件链** | 骨架已补，深度仍差 |
| 因子形态 | Docker 里的 Python + Qlib 数据模板 | 默认白名单表达式；**可选冻结 `python_sandbox`** | 半差距（无 Docker/CoSTEER） |
| 模型研发 | LSTM/GRU/PTNN、超参、与因子交替 | **thin equal-weight LS 头**，无架构搜索 | **仍差** |
| 因子–模型协同 | bandit/LLM 读指标选下一步 | **`loop` 交替两步**，启发式翻转，非读指标 bandit | 骨架已补 |
| 研报 | LangChain PDF + 截图 → 再实现 | finpdfpro 版面/公式 → 回测 → 偏差；`needs_impl` | **本侧更深** |
| 实现进化 | CoSTEER：错因摘要 + 相似失败 | 复现自愈；isolate 失败进 trace，**无相似失败检索** | 半差距 |
| 评测宇宙 | Qlib CSI300 长窗、组合分析 | 本地薄面板 / 米筐；门禁防伪 CSI300 | 本侧更诚实 |
| 知识 | Trace + coder 成败库 | catalog + wiki + **cites 链** | 半差距 |
| 人机复核 | 弱；看 UI trace | 晋升门禁、reject、audit | **本侧更强** |
| 交付 | 单包 + Docker | 3.12 壳 + 3.10 swarm 拓扑 | 各有债 |
| 基准数字 | MLE-bench、论文 ARR | 离线 pytest + 固定 PDF live | 他们有对外数字 |

---

## 4. 还剩的真差距（骨架已落地之后）

### 4.1 链在，过夜环不在（部分已落地）

`trace_events` 能复盘「后一步 cite 前一步」，且 hypothesis 正文已落 trace。过夜环已可经 JobRunner 异步 research_loop 承载（LLM 读历史为可选 advisor，默认启发式）。`finaince agent` 仍是一轮工具调用。

### 4.2 模型头太薄（按本档标准已补）

`loop` 的 model 步已是**可训练、可跳过**的 OLS 线性头（lag-1/lag-2，样本不足诚实 `skipped`）。RD-Agent 训 LSTM/GRU/PTNN；LightGBM 头是可选升级，不是门槛。

### 4.3 动作选择不读指标（已落地）

`choose_next_action` 已吃上一轮 `portfolio_return` / skip_reason：model 正收益续 model，跳过或非正回 factor。LLM 读整段历史是可选 advisor（`FINAINCE_LOOP_ADVISOR=1`），默认启发式且失败诚实降级。

### 4.4 失败不会被下一轮检索（已落地）

相似失败检索已落地（trace.recent_failures + SDK 工具 + playbook 纪律）。可选 bwrap/Docker 加固仍作为未来工作。

### 4.5 研报 `needs_impl` 还不会自动开沙箱

状态会打上，人要自己 `finaince impl`。缺：`needs_impl` → 生成 `compute(panel)` 草稿 → isolate → 同一套门禁。

### 4.6 没有可引用的同宇宙基准

没有锁定窗 + 成本 + 数据版本的对外数字。qlib 子进程在 3.12 上默认关。禁止用论文 ARR 当验收。

---

## 5. 不要学、或已经强于对方的

| 点 | 为什么留下 |
|----|------------|
| fail-closed 晋升 | RD-Agent 容易把「跑通的实验」当成可上线因子。本侧 `thin_panel` / `formula_proxy` / 空收益 是生产纪律。 |
| 中文研报流水线 | LangChain 切 PDF 对付不了券商双栏、公式、扫描件。`finpdfpro` 是差异化，不要换成通用 loader。 |
| 同源工作台 + doctor | RD-Agent 要 Docker 健康检查；研究员日常路径更短的是 `doctor` → `serve` → 点 Catalog。 |
| 诚实失败 | `no_factors`、裸 `discover` 拒绝伪装。qlib 本地 child 在可用 panel 上 `ok: true`；占位不再当作成功。 |
| 算子沙箱作默认 | 完全放开「自己 pip install 再写任意 Python」会毁掉可审计性。代码补丁必须是可选、隔离、过门禁的一层。 |
| 泛化成 Kaggle / 微调 LLM | RD-Agent 的广度不是量化台的 KPI。 |

旧笔记里「引入 Data Profiling / 让 FactorAgent 写完整 Python 文件」方向对，但**不能**作为无门禁的默认路径。

---

## 6. 改进方案（对齐有用部分，不重写成 RD-Agent）

总原则：把 R/D 环和 coder 进化**接在 catalog / review / jobs 后面**，不新开第三套引擎，不替换 finpdfpro。

### 阶段 A — 因果链（骨架 **已落地**）

`trace_events` + job/eval 挂钩 + `finaince trace` + `GET /api/v1/trace`。后一条 cite 前一条。

**还要**：工作台 Run 详情画出这条链；playbook 强制先 `GET /api/v1/trace`；事件上补可选 hypothesis 字段。

### 阶段 B — 受控代码因子（骨架 **已落地**）

`python_sandbox` + 冻结 `__import__` + catalog upsert + 原门禁。禁 pip。

**还要**：失败按 error 前缀检索最近 5 条再生成下一稿（轻量 CoSTEER）；可选 bwrap/Docker 加固，不是默认路径。

### 阶段 C — 因子–模型环（骨架 **已落地**）

`finaince loop` / `POST /api/v1/loop`：factor eval + equal_weight_ls 曲线。

**还要**：`choose_next_action` 吃 `portfolio_return`；model 步可换成线性/LightGBM，训不动就 `skipped`；SOTA = catalog `ready` 行。

### 阶段 D — 研报 → 实现（分类 **已落地**，自动补丁未做）

空抽取仍 `no_factors`；能描述不能跑 → `needs_impl`。

**还要**：`needs_impl` 自动起草 `compute(panel)` 再走 isolate，不要人手工粘代码。

### 阶段 E — 可引用基准（持续，doctor 可用性 **已落地**）

doctor / health 已报 isolator、qlib_child。

**还要**：固定窗快照与成本说明；只报能复现的数字。

---

## 7. 建议排期与明确不做

```text
已做    A 因果 cite 链 · B 冻结 isolate · C 两步 loop · D 状态分类 · E doctor 字段
        C' 组合指标选动作 + 可跳过线性头 · B' 相似失败检索（trace + SDK 工具 + playbook）
        D' needs_impl 自动 isolate · A' 工作台画出 trace · E' 锁窗基准（local_panel，非论文 ARR）
        过夜环：hypothesis 落 trace + JobRunner 异步 research_loop + 可选 LLM advisor
        顶级差距收口（2026-08）：eval cost_bps 净值口径 + weak_ic(t≥3)/inflated_sharpe(DSR) 门禁
        + 含成本锁窗 baseline · FINAINCE_PANEL_PATH 真宇宙面板注入 + doctor 面板报告
        · coaching.research_context（CSS 低相关采样 + CoE 失败教训）入 advisor prompt 与 SDK 工具
        · loop 表达式队列批量无人值守（CLI --expression / HTTP expressions）· model_head OLS/LightGBM 切换
        · review/adversary 子进程 fresh-context 重评比对，approve(adversary=true) 默认关
下一步  bwrap/Docker 沙箱加固 · 工作台 UI 呈现成本/门禁/对抗报告 · 真 CSI300 长窗对外数字（需数据轨凭据）
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

---

> 全景竞品对比（AlphaAgent / FAMA / AlphaMemo / AgonAlpha / BRAIN 生态等 11 家）见 `docs/competitor-analysis.md`（2026-08-21），含收口状态与采纳/拒绝决策；后续执行方案见 `docs/improvement-plan-v3.md`，逐步实现记录见 `docs/CHANGELOG.md`。

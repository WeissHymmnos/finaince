# FinAlpha 与前沿量化 Agent 竞品全景对比（2026-08）

| 字段 | 值 |
|------|-----|
| 日期 | 2026-08-21 |
| 方法 | exa 多轮检索 + 原始页面抓取（arXiv HTML/GitHub README/官方文档），逐项对照本仓源码 |
| 本侧 | FinAlpha（包名 `finaince`），fail-closed 中国卖方研究台 |
| 前置 | `docs/rdagent-gap.md`（RD-Agent 单品对比，2026-08-14）；本文是全景版并更新收口状态 |
| 原则 | 只记可验证事实（代码/论文/官方文档），宣传页数字单独标注；每条差距给出来源 |

---

## 1. 调研步骤记录

| 步骤 | 工具 | 内容 | 产出 |
|---|---|---|---|
| R1 | exa search | RD-Agent v0.8.0 changelog / 官方文档 / GitHub 主页 | §2.1 |
| R2 | exa search | TradingAgents、FinRobot Desktop、ai-hedge-fund 现状 | §2.8–2.10 |
| R3 | exa search | WorldQuant BRAIN 生态：wq-alpha-pipeline、wq-alpha-research skill、brain-mcp、Alpha Factory | §2.7 |
| R4 | exa search + arXiv HTML 全文 | AgonAlpha（2608.11250）架构细节、Agon 母系统（2606.24177） | §2.6 |
| R5 | exa search + arXiv HTML 全文 | AlphaAgent（2502.16789，KDD 2025）三正则化机制与消融 | §2.2 |
| R6 | exa search + ACL 全文 | FAMA（ACL 2024 Findings）CSS/CoE 算法伪代码与官方实现 | §2.3 |
| R7 | exa search + arXiv HTML 全文 | AlphaMemo（2606.20625）SSPM 记忆机制 | §2.5 |
| R8 | explore agent | 本仓文档 vs 代码现实审计（18 CLI / 20 HTTP / 12 门禁 / ~20 env） | 回填清单，已落实 handbook |

---

## 2. 竞品逐个深挖

### 2.1 RD-Agent(Q) v0.8.0（微软，NeurIPS 2025）——无人值守 R&D 工厂

- 场景：`rdagent fin_quant`（因子+模型联合）/ `fin_factor` / `fin_model` / `fin_factor_report`（财报→因子实现）。
- 进化深度：CoSTEER coder（`evolving_n=10`），错误摘要+相似失败检索驱动代码改写；动作选择 `bandit`（配置项可见）。
- 评测栈：Qlib CSI300，2008–2022，GeneralPTNN，TopkDropout（top50-drop5），**出厂带 open/close 成本+滑点配置**。
- 对外数字：约 2× ARR 于基准因子库、成本 <$10、因子少 70%（论文口径）；MLE-bench 榜首。
- 与我们的关系：Hermes 的 `rdagent-gap.md` 已逐条对照；本次收口了其中成本/多重检验两条。

### 2.2 AlphaAgent（KDD 2025，RndmVariableQ）——抗衰减三正则化

- 三机制：
  1. **AST 原创性**：最大同构子树相似度 vs Alpha101 因子库，重叠过高即拒；
  2. **假设对齐**：双重 LLM 一致性打分 `C(h,d,f)=α·c1(h,d)+(1−α)·c2(d,f)`——描述是否忠实假设、表达式是否忠实描述；
  3. **复杂度控制**：`R=λ1·SL+λ2·PC+λ3·ER`（符号长度+自由参数个数+特征数 log 惩罚）。
- Idea→Factor→Eval 三 agent 闭环；成败案例按失败模式入知识库。
- 数字（2021–2024 测试窗，Qlib+LightGBM 下游，top50）：CSI500 IC 0.0212 / AR 11.0% / IR 1.49；S&P500 AR 8.74% / IR 1.05；hit ratio 0.29 vs 0.16（+81%）、token 省 30%。对比组含 RD-Agent、AlphaForge、o1、DeepSeek-R1，全指标领先。
- 启示：**同质化是 LLM 挖因子的头号衰减源**；正则化要加在生成时，不是事后筛。

### 2.3 FAMA（ACL 2024 Findings）——多样性采样 + 经验链

- CSS：按**因子值** KMeans 聚类，各簇采样低相关因子作 in-context 样例（消融：样例数到 3 个前越多越好）。
- CoE：每簇一条经验链；新因子与链上因子相关最高处在链尾→扩展链，否则从匹配处分裂出新链；只有 γ 排名超过链上全部因子才入链。
- S&P500 RankIC 超 SOTA +0.006、RankICIR +0.105。代码真实可用（DeepSeek/OpenAI 兼容）。
- 启示：经验回灌要有**结构**（链、簇），不是把历史堆进 prompt。

### 2.4 AlphaForge（AAAI 2025）——生成式挖掘 + 动态组合

- G/P 双网络生成 RPN 编码公式因子（多样性惩罚内建）；第二阶段按近期 IC 动态加权组合，显式对抗因子衰减。
- CSI300 IC 4.40% / RankIC 5.89%。代码开源。

### 2.5 AlphaMemo（arXiv 2606.20625）——搜索过程记忆

- 记忆单元是 `(父因子上下文 z, 编辑动机 m)` 的充分统计量 `{n, μ, σ², (a⁻,b⁻)}`。
- 编辑动机从**父子 AST diff** 提取（先规范化：交换子树排序、窗口参数分桶、一元包装归一）。
- **置信门控残差融合**：记忆只学「相对 base prior 的残差」，`λt·ct(z,m)·Δt`，证据稀疏/高方差时贡献归零——记忆永远不会悄悄取代搜索先验。
- **非对称否决**：正记忆只软加分；负模式用 Beta 失败后验，高置信才硬否决（失败模式跨 regime 更稳定）。
- 启示：这是「trace 教训回灌」的严格版——有信用分配、有安全阀。

### 2.6 AgonAlpha（arXiv 2608.11250）——artifact 搜索 + 对抗评审 + 预算调度

- 搜索单元是冻结 artifact `A=(hypothesis, expression, evidence, rationale, verdict)`；lineage 链；唯一搜索算子 `extend(ℓ)`。
- proposer：每次 16 候选 halving tournament；自相关门 0.85 **前置到排序前**；负分入围者 sign-reflection（美元中性因子取反后 Sharpe 反号、换手不变，精确推导免重模拟）；单调目标约束（提交式必须超祖先最优）。
- reviewer：fresh-context + 不同模型路由，只拿工作目录和平台文档，唯一任务是找拒绝理由，可重跑模拟；**只有 verified fabrication 清零分数**，其余发现挂 artifact 作 advisory。
- scheduler：pending-aware MCTS（progressive widening + percentile rewards + backpressure），在途评估影响后续分配；10-worker 并行验证。
- 实战：WorldQuant BRAIN 上 5 人 6 后端，60 提交 17 个 SPECTACULAR（最高 Fitness 9.50 / Sharpe 3.48），全链路 prompt/evidence 公开。
- 规模：两角色共 101 行 prompt。母系统 Agon 还有 deep-literature 循环（每主题读 400–2000 篇）与 idea/proposal/experiment 工厂。

### 2.7 WorldQuant BRAIN 生态——平台裁决 + 众包规模

- 平台：~85,000 数据字段；API 限 3 并发；统一裁决数据、模拟器、门禁与评级（Fitness/Sharpe/turnover/自相关/sub-universe）。
- wq-alpha-pipeline：模板×字段网格一夜 ~800 次模拟，SQLite WAL 全落库，**PnL 差分后算相关**再贪心去重，手动提交防烧名额。
- wq-alpha-research skill：SKILL.md 自进化 playbook（失败蒸馏成规则），宣称 4 天零人工到 Gold Medal。
- worldquant-brain-mcp：MCP server + AST 表达式解析 + novelty detection + experiment memory。
- Alpha Factory v0.2.0：模板/LLM 双模生成、static lint（算子 arity/lookahead/括号）、SHA256 去重、DuckDB 存储、winner memory、变体 mutator、14 点验收清单、熔断开关。

### 2.8 TradingAgents v0.3.1（Tauric）——交易决策多智能体

- 分析师/多空辩论/风控/PM 七角色做**交易信号**，非因子研究；decision log + 反思注入后续同 ticker prompt；LangGraph checkpoint 续跑。README 明说两次运行结果不可复现。**定位差，不追。**

### 2.9 FinRobot Desktop v0.1.0（AI4Finance）——股票研报自动化

- ~18.4 万行；9 agent（lead+5 流水线+3 辩论）；**确定性计算分离**（30 个纯 Python 估值算子，LLM 只叙述）；7 数据源 failover；证据链接+数值溯源的 13 章研报。**定位差（个股研报域），但其「确定性计算/LLM 叙述分离」原则与我们一致。**

### 2.10 ai-hedge-fund（virattt）——教育型 AI 对冲基金

- 重构中：fund 作为一等实体（mandate 文件）、投资人 agent 变成可回测 alpha models、paper-trade 可选实盘。无 fail-closed 晋升概念。**参考其 mandate 抽象，不追其余。**

---

## 3. 维度化差距表（2026-08-21 收口后状态）

| # | 维度 | 顶级水位 | FinAlpha 现状 | 判定 |
|---|---|---|---|---|
| 1 | 成本模型 | RD-Agent 出厂带成本+滑点；BRAIN 卡 turnover/margin | `EvalRequest.cost_bps` 净值口径（换手来自 equity parquet），baseline 支持含成本双跑 | ✅ 已收口（对外数字待真数据） |
| 2 | 多重检验纪律 | Harvey-Liu t≥3、Deflated Sharpe 是行规 | `weak_ic`（t≥3）+ `inflated_sharpe`（DSR≥0.95，n_trials 从 trace 估计）晋升门禁，可 override | ✅ 已收口 |
| 3 | 数据宇宙 | BRAIN 8.5 万字段；Qlib CSI300 2008+ | `FINAINCE_PANEL_PATH` 注入任意面板 + 米筐轨 + doctor 面板报告；默认仍是 2 股 smoke | 🟡 能力已补，可比数字未产出 |
| 4 | 多样性正则 | AlphaAgent AST 同构原创性+复杂度惩罚；FAMA 聚类采样 | `coaching.diverse_expression_samples` 贪心低相关采样（收益序列级）；无 AST 结构相似度、无复杂度惩罚 | 🟡 半差距 |
| 5 | 经验回灌结构 | FAMA CoE 链分裂/扩展；AlphaMemo 残差记忆 | `research_context` 把样例+教训注入 advisor prompt 与 SDK 工具；无链式结构、无置信门控 | 🟡 半差距 |
| 6 | 错误驱动进化 | CoSTEER evolving_n=10 代码级改写 | `recent_failures` 前缀检索 + playbook 纪律；表达式级，非代码级 | 🟡 半差距 |
| 7 | 对抗验证 | AgonAlpha fresh-context reviewer（LLM 五维审计+重执行+veto） | `review/adversary.py` 子进程 fresh-context **确定性重评**+容差比对+veto，approve 默认关 | ✅ 已收口（差异：我们查数值造假，他们连叙事一起审） |
| 8 | 吞吐与调度 | 一夜 800 sim；pending-aware MCTS 预算分配 | loop 表达式队列批量 + JobRunner 异步；单进程串行，无并行预算概念 | 🟡 半差距 |
| 9 | 因子–模型联合 | RD-Agent 全模型编码进化（GeneralPTNN 等） | loop 双步交替 + `model_head` OLS/LightGBM walk-forward 切换 | 🟡 半差距 |
| 10 | PDF→因子 | X2Strategy/FactorEngine 分段抽取 | finpdfpro 版面/公式级解析+偏差自愈+反思循环，中文券商研保真 | ✅ 我方更强 |
| 11 | 人机复核/审计 | 各家基本自动采纳 | promote→review→approve fail-closed、synthetic 标记、audit 哈希链、HTTP 禁 override | ✅ 我方更强 |
| 12 | 诚实失败语义 | TradingAgents 自认不可复现 | no_factors/thin_panel/占位 ok=false/诚实降级 everywhere | ✅ 我方更强 |

## 4. 采纳 / 拒绝决策

**采纳（按性价比排序）：**
1. **AST 结构相似度去重 + 复杂度惩罚**（AlphaAgent）：catalog 已存表达式文本，Python `ast` 或自写算子树解析即可；直接提升 hit ratio 的最便宜手段。
2. **CoE 经验链结构化**（FAMA）：把 coaching 的 lessons 升级为按簇维护的链（扩展 vs 分裂），advisor prompt 引用链而非散点。
3. **sign-reflection**（AgonAlpha）：负 IC 因子精确推导反向指标，省一半模拟预算。
4. **pending-aware 批量调度**（AgonAlpha 简化版）：JobRunner 记录在途 job，loop 批量提交时避开同一 panel 的重复计算。
5. **PnL 差分相关**（wq-pipeline）：核对 `gates.correlated` 是否已用日收益差分——若用累计净值需修。

**拒绝：**
- 交易决策多智能体（TradingAgents 域）、个股研报自动化（FinRobot 域）——产品定位不同。
- LLM 产数字的一切路径（我们确定性引擎产数、LLM 只叙述的原则不破）。
- Alpha-GPT 系（无代码无可复现数字，vaporware）。

## 5. 下一步路线建议

> 本节已细化为可执行方案：`docs/improvement-plan-v3.md`（七个工作流，含现状锚点/设计/契约/测试/验收/工作量）。

```text
已收口（2026-08 两轮）成本+多重检验 · 面板注入 · 教练模块 · 批量环 · 对抗评审 · GBM 头
下一步  A. AST 原创性+复杂度门禁（生成时正则化）
        B. CoE 经验链结构化
        C. sign-reflection + pending-aware 批量调度
        D. 真 CSI300 长窗对外数字（依赖数据凭据）
不做    交易信号域 · 研报自动化域 · LLM 产数字 · vaporware 对标
```

## 6. 信息源

| 来源 | 链接 |
|---|---|
| RD-Agent releases/docs | github.com/microsoft/RD-Agent · rdagent.readthedocs.io（quant_agent_fin） |
| AlphaAgent | arxiv.org/abs/2502.16789 · github.com/RndmVariableQ/AlphaAgent |
| FAMA | aclanthology.org/2024.findings-acl.233 · github.com/liu-wei2021/Alpha_mining_FAMA |
| AlphaForge | github.com/DulyHao/AlphaForge（AAAI 2025） |
| AlphaMemo | arxiv.org/abs/2606.20625 · github.com/jarrettyu/AlphaMemo |
| AgonAlpha / Agon | arxiv.org/abs/2608.11250 · arxiv.org/abs/2606.24177 |
| BRAIN 生态 | github.com/angel4angelov-glitch/wq-alpha-pipeline · github.com/QuantML-Research/wq-alpha-research · github.com/duy0699cat/worldquant-brain-mcp · huggingface.co/gaurv007/alpha-factory |
| TradingAgents / FinRobot / ai-hedge-fund | github.com/tauricresearch/tradingagents · github.com/AI4Finance-Foundation/FinRobot · github.com/virattt/ai-hedge-fund |

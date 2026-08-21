# FinAlpha 实现日志（2026-08 会话全记录）

> 本文是 2026-08-21 各工作会话的逐步实现记录。所有改动当时未分次 commit，git 历史不承载这些细节；本文是唯一完整台账。每条含：改了什么、在哪个文件、为什么、如何验证。测试基线从会话开始的 **136 passed, 1 skipped** 演进到 **178 passed, 2 skipped**。

---

## 会话一：接续 Hermes 路线（trace 记忆 + 过夜环）

### 1.1 Trace 层：hypothesis 列 + 相似失败检索（rdagent-gap B'）

- `src/finaince/trace.py`
  - `trace_events` 新增 `hypothesis TEXT` 列，迁移用 `PRAGMA table_info` + 条件 `ALTER`（与 jobs 表同模式），旧库无损升级。
  - `append_event(..., hypothesis=None)` 新关键字参数；`list_chain()` 行恒含 `hypothesis` 键。
  - 新函数 `recent_failures(error=None, *, limit=5)`：只取失败事件（error 非空），按 error 前缀归一化匹配（冒号前、去空白、大小写不敏感、双向前缀），最新在前。
- 新测试 `tests/test_trace_chain.py`：hypothesis 存取、旧 DDL 兼容（手工建 10 列旧表再 ALTER）、检索过滤/limit/排序。

### 1.2 Loop 层：LLM advisor + 异步 job 修复（rdagent-gap 4.1）

- `src/finaince/loop.py`
  - 新增 `advise_action(history)`：默认启发式（复用 `choose_next_action`）；`FINAINCE_LOOP_ADVISOR=1` 时先问聊天 LLM（经 `runtime.resolve_llm`，要求 api_key/base_url/model 三要素齐全，缺一即回落），prompt 含最近 ~10 条事件；任何失败回落启发式并带 `advisor_error`。永不抛错。
  - `run_loop` 每步向 `append_event` 传 `hypothesis=`（因子步/模型步各一句事实性理由），`extra.via` 记录决策来源。
- `src/finaince/jobs/runner.py`：修复 `run_loop_job(sync=False)` 永远 queued 的桩——改为 reproduce 同款 detached-child（`start_process` + 子进程 `finaince loop --steps N --sync`，靠 `FINAINCE_JOB_ID` 回写同一行）。
- `src/finaince/cli.py`：`loop` 命令加 `--sync/--async`。
- `src/finaince/serve.py`：`POST /api/v1/loop` 接受 `{"sync": false}`。
- 质量修正（人工复核子代理产出）：删除硬编码的 `gpt-4o-mini` 与 OpenAI URL 回退（违反「不假设厂商」纪律）；os/json 提到模块顶部；清理尾随空格。

### 1.3 工具面与纪律

- `src/finaince/tools.py`：`handle_recent_failures(error, limit)`（limit 夹取 1..50，结构化错误）。
- `src/finaince/sdk_ext.py`：注册 SDK 工具 `recent_failures`。
- `src/finaince/agent_playbook.py`：SYSTEM_PROMPT 增三条纪律（重写实现前必查同类失败并引用 id；过夜研究走 loop/sync=false；汇报必须引用 trace 事件 id）。
- `docs/rdagent-gap.md`：§4.1/4.4/§7 状态更新。

验证：146 passed, 1 skipped。

---

## 会话二：顶级平台差距六项收口

### 2.1 成本模型 + 多重检验门禁（差距 1）

- `src/finaince/eval/router.py`
  - `EvalRequest.cost_bps: float = 0.0`。>0 时从 equity parquet 读 `ls_return_raw` 与 `turnover`，净值口径 `raw − cost_bps/10000 × turnover_t` 重算 sharpe/回撤/年化；metrics 增 `cost_bps/turnover_mean/sharpe_net`；换手不可得时告警 `cost_not_applied_no_turnover` 并如实记录未应用。（核实：repro_polars 本地路径换手数据真实存在于 equity_curve.parquet。）
- `src/finaince/review/gates.py`
  - `ic_t_stat(ic_ir, n_days)`（Harvey-Liu：|t|=|ICIR|·√n_days <3 fail，样本不足 skip 带 detail）。
  - `deflated_sharpe(returns, n_trials)`（Bailey–López de Prado 2014；SR0 用极值理论期望、偏度/峰度来自收益序列、Φ⁻¹ 用 `statistics.NormalDist`；<20 观测返回 None）。n_trials = 1 + trace 中 eval/isolated_impl 事件数。
  - 新门禁 `weak_ic`、`inflated_sharpe` 接入 `evaluate_gates`，skip-not-fail 语义，可 override。
- `src/finaince/review/desk.py`：override 名单扩展两个新门禁。
- `src/finaince/baseline.py`：`run_locked_baseline(cost_bps=…)` 输出含成本口径，双跑确定性保持。
- 新测试 `tests/test_cost_gates.py`（成本数学/t 统计/DSR 手工对照/门禁流/override/baseline 确定性）。

### 2.2 真宇宙面板注入（差距 2）

- `src/finaince/runtime.py`：`panel_path()`（env `FINAINCE_PANEL_PATH` 校验存在+可读+核心列，否则回落打包面板）；`local_data_path()/qlib_local_data_path()` 改经它路由（env 未设时零行为变化）；`panel_stats(path=None)` 泛化并补 start/end。
- `src/finaince/settings.py`：doctor 增 `panel_path/panel_stats/ricequant_creds/universe_claim_warning`。
- `README.md`/`README.zh-CN.md`：新增「Comparable numbers（真宇宙数字）」节。
- 新测试 `tests/test_data_track.py`。

### 2.3 教练模块（差距 3，FAMA CSS/CoE 简版）

- 新 `src/finaince/coaching.py`：
  - `diverse_expression_samples(limit)`：贪心 CSS——按 |IC| 降序起步，迭代选与已选集最大相关最小者；日期对齐 <10 个重叠或零方差记 corr=1.0。
  - `failure_lessons(error_prefix, limit)`：包装 `recent_failures` 为 `{id, error_head, summary_short, hypothesis}`。
  - `research_context(...)`：合成块，永不抛错。
- `tools.handle_research_context` + SDK 工具 `research_context`；playbook 增「提新因子前先调 research_context」。

### 2.4 模型头模块（差距 6）

- 新 `src/finaince/model_head.py`：`train_head(returns, kind=None)`；kind 来自参数或 env `FINAINCE_MODEL_HEAD`（ols 默认）；ols 委托 `loop.train_linear_head`（行为逐位一致）；gbm 走 lightgbm→sklearn HistGB→诚实跳过（`gbm_unavailable`），expanding-window walk-forward（禁止 in-sample 自欺），<15 行跳过，fit 异常包成 `{backend}_failed:{exc}`。
- `pyproject.toml`：新 optional extra `gbm = ["lightgbm>=4.0"]`（不动既有 extras）。
- 新测试 `tests/test_model_head.py`（委托一致性/env 解析/无库跳过/fake lightgbm 注入/行数不足/崩溃降级）。

### 2.5 Loop 批量化（差距 4）

- `src/finaince/loop.py`：`run_factor_step(expression=…)` 参数化；`run_loop(expressions=[…])` 表达式队列，耗尽时诚实降级 `expression_queue_empty`；结果增 `expressions_evaluated`；模型步改调 `model_head.train_head`；advisor LLM prompt 注入 `coaching.research_context` 前 3 样例 + 3 教训（失败静默跳过不影响启发式）。
- `jobs/runner.py`：payload/argv 透传 expressions（`--expression` 重复）。
- `cli.py`：`--expression` 多值 Option。
- 测试并入 `tests/test_rdagent_gap.py`。

### 2.6 对抗评审（差距 5，AgonAlpha 式）

- 新 `src/finaince/review/adversary.py`：`adversarial_review(promotion_id, tol_rel=0.05, tol_abs=0.01)`——**子进程全新解释器**重跑同参评测（配置经 argv JSON 传递，无 shell 插值），五项检查：reexec_ok / ic_match / sharpe_match / not_proxy / returns_present；仅全过才 approved；超时/崩溃/非 JSON → 对应检查失败；落一条 `adversary_review` trace 事件（verdict/n_checks 进 `_slim_metrics` keep 名单）。
- `review/desk.py`：`approve(..., adversary=False)` 关键字开关；拒绝→`{"ok":false,"error":"adversary_rejected"}` 行留 review；通过→报告嵌 gate_json.adversary + audit 标记。默认关，零行为变化。
- 新测试 `tests/test_adversary.py`（真实 evaluate 播种 → happy path；篡改 IC → tamper 拒绝；proxy 拒绝；默认路径不变；trace 恰一条）。

### 2.7 HTTP 收口（本人完成）

- `serve.py`：approve body 支持 `{"adversary":true}`；新端点 `POST /api/v1/review/{id}/adversary`（只出报告）；`POST /api/v1/loop` 支持 `expressions` 数组（清洗空串）。
- `docs/rdagent-gap.md` §7 收口状态更新。

验证：169 passed（Phase 1 后）→ **178 passed, 2 skipped**（全部落地）；HTTP 冒烟 loop sync:false 返回 running job；4 条 review 路由注册确认；lsp_diagnostics 无新增错误。

---

## 会话三：竞品全景调研 + 文档审计回填

### 3.1 调研（exa 八轮，步骤记录于文档 §1）

- 产出 `docs/competitor-analysis.md`：11 家竞品机制级深挖（AgonAlpha artifact 五元组/halving tournament/sign-reflection/pending-aware MCTS；AlphaAgent 三正则化与消融数字；FAMA CSS/CoE 算法；AlphaMemo SSPM/非对称否决；BRAIN 生态四项目；RD-Agent v0.8.0 配置；TradingAgents/FinRobot/ai-hedge-fund 定位判定）。
- 12 维差距表标注 ✅已收口/🟡半差距/✅我方更强；采纳/拒绝决策清单。

### 3.2 文档审计与回填（explore 审计驱动）

审计结论：handbook 仅覆盖 13/18 CLI、14/20 HTTP、6/12 门禁，且 `weak_ic` 描述实质性张冠李戴。回填：

- `docs/handbook.md`
  - §6 门禁表：纠正 `weak_ic`（t 统计量，非 |IC|≤0.005）、补 `ic_threshold/simulated/empty_code/inflated_sharpe/corr_error:*`、修 `correlated` 双向对照描述、注明 override 名单。
  - 新增 §6.1「研究循环与对抗评审」。
  - §5 命令表：补 trace/loop/impl/baseline/agent/sdk-info/sdk-query 行及 discover/reproduce/eval/catalog/library/jobs/doctor/review 的缺失选项。
  - §7 HTTP：补 catalog/{id}、jobs/{id}、jobs/{id}/cancel、review reject/adversary、impl/needs、approve adversary body、loop expressions。
  - §8 env：补 PANEL_PATH/BT_START/BT_END/CATALOG/CATALOG_MEMORY/LOOP_ADVISOR/MODEL_HEAD/QLIB_*/LOCAL_DATA_PATH/AIMINER_PYTHON/INCLUDE_SPA/JOB_ID 等。
- `docs/platform-improvement.md` / `-v2.md`：顶部勘误注（从未实现的 `POST /api/v1/jobs` 与 review 子命令语法，指向 handbook 为准）。
- 注：backfill 子代理因模型后端许可故障（403）失败，由主会话亲自完成。

---

## 会话四：改进方案 v3

- 四路并行勘察（生成端正则化现状 / 调度与工作台现状 / 数据轨现状 / bwrap+RQ 外部实践）。
- 产出 `docs/improvement-plan-v3.md`：七个工作流（WS-A 生成端正则化 AST 原创性+复杂度惩罚、WS-B 经验链结构化、WS-C 吞吐调度、WS-D CSI300 数字轨、WS-E bwrap 沙箱层、WS-F 工作台呈现、WS-G 质量），每项含现状 file:line 锚点、设计、接口契约、测试、验收信号、工作量、依赖；排期依赖图与三个里程碑。

---

## 会话五：v3.1 计划全量落地（WS-A…L 十二工作流）

> 依据 `docs/improvement-plan-v3.md`（含 v3.1 审查修订）。委派后端两次 403（Gemini Code Assist 许可故障）后全部由主会话亲自实现。测试基线 **178 → 247 passed, 2 skipped**。

### Wave 1（第 1 周）

- **WS-A 生成端正则化**：新 `src/finaince/expr_ast.py`——双方言函数调用表达式解析（剥 `$` 后 stdlib ast）、规范化（交换算子子树排序 / 一元幂等折叠 / 窗口粗分桶仅入哈希不入相似度）、`similarity`（精确公共子树 Dice + 容参自顶向下对齐取 max，自身=1、窗参变体∈(0.5,1)、无关≈0）、`complexity{sl,pc,fc}`。种子库 `tests/fixtures/alpha_seed_zoo.json`（30 条经典因子，逐条可解析有测试）。门禁 `homogeneous`（max_sim>0.85，corpus 排除自身 id）/`overcomplex`（sl>40∨pc>6∨fc>8）进 `evaluate_gates`，override 语义沿用；`catalog/store.py` 增 `expr_hash` 列（ALTER 迁移 + 表达式索引 `find_by_expr_hash`）；`discovery.cull_factor_pool` 入口结构去重（近重复淘汰记 obs 事件，不可解析放行交正则校验）。
- **WS-C 吞吐**：`eval/router.py` 进程级 panel 缓存——spike 结论 reproagent `build_backtest_bundle` 不吃显式股票池且内部自建 DataLoader，按计划 monkeypatch 条款包装 `DataLoader.load_price_data`（仅 local 后端；键 = 数据源+universe+窗口+parquet 路径/mtime/size；命中 clone 防污染；`FINAINCE_PANEL_CACHE=off` 关闭；`_PANEL_CACHE_STATS` 可测）。`jobs/runner.py` 新 `active_jobs/can_submit/max_concurrent_jobs`：同 dedup_key 在途 job 拒绝（`duplicate_pending`），`FINAINCE_MAX_JOBS`（默认 2）超限拒绝（`max_jobs_reached`）；loop/reproduce payload 注入 dedup_key。sign-reflection `reflect_sign`：镜像行 IC/RankIC/Sharpe/ICIR/perf_metric 精确变号、turnover/回撤幅值不变，负 IC 且 |t|≥3 才镜像，None=不满足条件。
- **WS-D 数字轨**：新 `src/finaince/data_track.py`——独立 polars 向量化轻回测（IC/RankIC/ICIR + 五分位 LS 含双边成本换手），不走 bundle；缓存 `$FINAINCE_HOME/data_track/csi300/v1/{year}/csi300_{year}.parquet+.sha256` + constituents/components_{date}.parquet + `.manifest.json`，读取验哈希 fail-closed 报缺年份清单；point-in-time 成分 = 最近调仓日快照（6/12 月第二个星期五）；live 抓取 rqdatac 门控（无凭据 RuntimeError）。`finaince bench` CLI + `GET /api/v1/bench`（provenance: window/cost_bps/universe_source/data_version + markdown 渲染）+ doctor `data_track` 段。

### Wave 2（第 2 周）

- **WS-E bwrap 层**：`isolate.py` 两层沙箱——`FINAINCE_SANDBOX=bwrap|auto|off`（默认 auto）；bwrap 参数含 `--unshare-all --share-net --ro-bind venv/pkg --tmpfs /tmp --proc /proc --dev /dev --new-session --die-with-parent`（防 TIOCSTI）；bwrap 失败自动回落 L1 并在结果标 `sandbox_fallback/sandbox_fallback_reason`；结果 dict 恒带诚实 `via` 标注实际层；doctor `sandbox_backend` 段。
- **WS-H 代码因子进化**：新 `src/finaince/code_evolution.py`——LLM 写完整 compute(panel) → 沙箱子进程 → upsert+evaluate_gates 全门禁（不自动晋升）；失败轮用 error 前缀教训 + `ast_edit_motive`（difflib+AST 签名）构建改写 prompt；无 provider 停 `llm_unavailable` 不假装迭代；trace 事件 `code_evolution` 带全审计链。
- **WS-I 组合目标环**：新 `src/finaince/combination.py`——跨 ready 因子滚动逆波动率每日权重（严格 t-1 信息，扰动未来值不改历史权重有测试），换手付双边成本，`combo_vs_best_member` 给超越判定；`loop.choose_next_action` 奖励升级为净 Sharpe（legacy portfolio_return 回落），模型步自动尝试组合并透出 `combination` payload。
- **WS-J BRAIN 外部裁决轨**：新 `src/finaince/brain_track.py`——auth→POST /simulations→Location 轮询→GET /alphas/{id} 取 grade/is 指标→写回 catalog tags（brain:{alpha_id}、brain_grade:*）+ trace 事件；`adjudicate()` 每条路径声明 `adjudication_level`（platform / none），无凭据降级附 `degraded_to` 指向 WS-D bench；CLI `finaince brain-submit`。

### Wave 3

- **WS-K 语料战役**：新 `src/finaince/corpus_campaign.py`——manifest 断点续跑（done/no_factors 终态跳过、failed 可 reset 重试、消失文件标 skipped_missing）；`classify_outcome` 把 no_factors 定为诚实终态；stats 含 factors_cataloged 总账；CLI `finaince campaign`。
- **WS-L 过程记忆**：新 `src/finaince/process_memory.py`——信用分配 = 门禁存活（survived ±2/−1 置信加权，权重夹 [-10,10]）；motif 按 (错误类, AST-diff 形状) 签名去重；`context_block` 注入 advisor prompt（advise_action 已接线）；经验链展示层并入（FAMA 式链尾规则：corr≥0.7 且 RankIC 超全链才扩展，否则拒绝或新建链；修复持久化丢 returns 的 bug）；`research_context` 增 chains 字段。
- **WS-F 工作台**：`serve.py` 新只读端点 `GET /api/v1/review/{id}/gates`（404/鉴权齐全）；stub SPA 升级三栏（Catalog 含 cost/sharpe_net 标签、Review 队列 gates/adversary/approve/reject 按钮、Trace hypothesis 时间线），纯静态零构建链，HTML 注入转义。

### Wave 4

- **WS-G 质量**：`eval/dialects.py` 全覆盖（is_listed 白名单边界、translate 双向 roundtrip、attach_translation 矩阵）；`run_loop` 真 eval e2e（无 mock，走 local panel）与队列耗尽降级断言；ruff 进 `[dev]` extra + `[tool.ruff]` 配置（E4/E7/E9/F/I，line-length 110）+ offline CI lint 步（continue-on-error，存量告警不阻塞）；本会话新文件 ruff --fix 清零。

### 测试基线

178 → 205（Wave1）→ 226（Wave2）→ 241（Wave3）→ **247 passed, 2 skipped**（最终）。既有测试适配三处均为计划语义内：desk/thin 流加 `homogeneous` override（先例：weak_ic/inflated_sharpe 当年同样处理）；adversary 夹具表达式从 `Rank(Delta(close,1))` 改 `Rank(Delta(close,7))`（原式是种子 rev_04 的符号镜像，属真阳性拦截）；SPA stub 断言从 `id="root"` 更新为三栏 id。

---

## 会话六：全面质量审计 + 修复

> 两路并行 explore 审计（存量核心 / 七个新模块）→ 主会话逐条复核（驳回 2 个误报）→ 按严重度修复 → 第三路 explore 逐项验证 9/9 VERIFIED → 复查发现的 2 个残余问题当场修复。测试基线 **247 → 259 passed, 2 skipped**。

### P0：数字口径统一
- 新增 `domain/scoring.py` 四函数单一实现：`sharpe_ratio` / `max_drawdown` / `equity_curve` / `ic_t_stat`（模块 docstring 声明「性能数字只准从这里出」的治理契约）。消费端全部改接：`combination.py`（三处内联）、`data_track.py _window_metrics`、`loop._equity_curve`、`gates.ic_t_stat`（委托）、`discovery.reflect_sign` t 统计。消除 Sharpe×4 / 回撤×3 / 净值×2 / IC-t×2 的口径分裂。
- `data_track._layered_long_short` 改返回**逐日换手序列**，`_window_metrics` 逐日扣成本（对齐 router 口径）；provenance 增加 `turnover_model` 字段声明 churn 是参与率代理而非美元换手。

### P1
- `jobs/runner.py`：新增 `idx_jobs_status(status,kind)` 索引 + ALTER 显式 commit；`active_jobs()` 改 SQL `WHERE status IN (...) AND kind=? ORDER BY` 参数化查询（复查时把初版 str.replace 写法改为拼接参数化，消除脆弱性）。
- `corpus_campaign.save_manifest`、`process_memory.save_memory/save_chains` 改原子写（pid 后缀 tmp + `os.replace`，防崩溃损坏与并发覆盖——后者为复查发现并当场修复）。
- `brain_track.brain_base()` 改调用时读 env（原 import 时常量）；`_poll_simulation` 错误带响应体 detail。
- env 双读收敛：`runtime.raw_local_data_root()` 为 FINAINCE_LOCAL_DATA_PATH|LOCAL_DATA_PATH 的规范双读，`router._local_panel_identity` 改用之。
- `process_memory.update_chains` 按 FAMA 规则改为扫描**全链成员**取最大相关（原来只对比尾成员，语义偏离计划），decision 带 `best_member_position`；docstring 明确「扩展仍只在链尾、RankIC 仍须超全链」。

### P2
- 删除死代码 `isolate._naive_ic`；简化 `expr_ast._aligned_score` 冗余条件为长度比较；`code_evolution` stage 标签逻辑改写清晰。
- `serve.py` 校验收紧：promote direction 枚举（400）、approve adversary 严格布尔（400）、impl source 上限 20000 字符（413）。

### 验证
- 全量 **259 passed, 2 skipped**（+12 个回归测试：scoring 一致性、逐日成本、_bucket 边界 11 值、_turnover 定义、内部成员链匹配、原子写、brain_base lazy、serve 400 路径）。
- Ruff E/F/I 仅剩 5 条存量（可选依赖探针×4 + legacy F841×1），本会话代码零告警。
- 复核驳回的审计误报（记录在案防止回归恐慌）：`expr_ast._bucket` value>180 并非 IndexError（bisect 上界=6 合法）；`brain_track` finally 无未绑定风险。

---

## 关键不变量（全程遵守）

1. 默认行为零破坏：每个工作单元落地时既有测试全绿（136→146→169→178 单调递增）。
2. 诚实失败：一切降级路径显式（advisor_error / insufficient_* / gbm_unavailable / cost_not_applied_no_turnover / expression_queue_empty / adversary_rejected）。
3. 不假设厂商：LLM 解析要求三要素齐全，无硬编码模型名/端点。
4. hermetic CI 不碰网络：live 能力全部 `-m live` 或凭据门控。
5. 门禁 fail-closed：新门禁 skip-not-fail 只用于「数据不足」，不用于「数据难看」。

# FinAlpha

[English](README.md) · **中文**

FinAlpha 是一个专门用于管理卖方研报中选股因子的专业工具。它提供了一套完整的工作流程：将收集到的选股因子录入到因子目录（catalog）中，然后在本地数据面板（panel）上进行系统性的评测和验证。只有通过门禁检测的因子才能获得晋升，进入下一阶段。同时，对于卖方研报的复现，系统集成了 `reproagent` 和 `finpdfpro` 两个工具，可以准确还原研报中的版面布局和数学公式。

> **注意**：Python 安装包的名称是 `finaince`（历史原因），但产品的正式名称是 **FinAlpha**。在安装和导入时请使用包名 `finaince`，但在文档和讨论中我们统一称呼其为 FinAlpha。

**[完整的产品手册](docs/handbook.md)** 包含了详细的使用教程、所有命令的完整说明、HTTP API 接口约定以及系统工作台的界面截图，推荐新用户先阅读手册以全面了解系统功能。

![填好 desk token 后的 Catalog 界面](docs/handbook/images/01-catalog.png)

作为安装验证的基准测试，`finaince baseline` 命令会在系统自带的测试数据集（包含两只股票的 `local_panel`）上运行一个预设的因子表达式 `Rank(Delta(close, 1))`。整个测试过程的交易成本设定为 0 bps（基点）。测试输出的各项指标数值仅用于验证系统安装是否成功，不具备实际投资参考价值。

## 许可证与版权

本项目采用 [GNU Affero General Public License v3.0](LICENSE) 许可证发布。这是一个强 copyleft 许可证，要求所有基于本项目的修改和分发必须以相同许可证的形式公开源代码。

版权信息和第三方引擎的使用声明，请参阅 [NOTICE](NOTICE) 文件。

---

## 公开安装（要求 Python 3.12）

FinAlpha 是一个完全开源的项目，您只需要克隆这个公开仓库即可完成所有依赖的安装。系统的核心引擎会从 GitHub 上的固定 commit 版本进行安装，所有依赖配置都在 `pyproject.toml` 文件的 extras 部分中定义。

> **安全提示**：默认情况下，系统不会从开发者的本地机器注入任何额外的源码。如果您需要在开发环境中使用本地源码（例如调试或修改代码），可以设置环境变量 `FINAINCE_PATH_HACK=1` 来启用这一功能，但请谨慎使用，确保您理解这可能带来的安全风险。

### 安装步骤

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
cp .env.example .env   # 先配置 FINAINCE_DESK_TOKEN，再运行 finaince serve
finaince doctor
```

### 安装验证

运行 `finaince doctor` 命令可以检查系统是否安装正确。该命令只有在 JSON 响应的 `ok` 字段为 `true` 时才会返回退出码 0，表示所有检查都已通过。

安装完成后，请确保以下 Python 导入语句都能成功执行：
- `import finaince`
- `import reproagent`
- `from aiminer.manager import cull_alpha_pool`

### 环境变量配置

系统依赖以下环境变量来正常运行：

| 环境变量 | 用途 | 说明 |
|---|---|---|
| `FINAINCE_DESK_TOKEN` | API 认证 | 读取 catalog、review 以及所有 HTTP 写操作都需要此 token |
| `FINAINCE_ALLOW_PUBLIC_BIND` | 服务绑定 | 设置为 `1` 可以将服务绑定到 `0.0.0.0`，默认仅绑定 `127.0.0.1:8000` |
| `FINAINCE_PACKAGED_SPA` | 前端配置 | 设置为 `1` 时，即使存在编译好的 `aiminer/frontend/dist`，也只使用简单首页 |
| `FINAINCE_PATH_HACK` | 开发模式 | 设置为 `1` 时会注入开发者机器上的本地源码 |

### 可选的依赖包

根据您的具体需求，可以选择性地安装以下 extras 包：

- `.[agent]`：集成 Claude Agent SDK，用于智能代理功能
- `.[dev]`：包含 pytest 等测试工具，用于本地开发和测试
- `.[all]`：安装所有可选的依赖包

### 前端说明

wheel 包中默认只包含一张简单的首页（`finaince/web/index.html`）。如果您需要完整的 Catalog 和 Review 管理界面，需要使用编译好的 `aiminer/frontend/dist` 前端资源。当检测到旁边存在 dist 目录时，系统会自动使用完整的前端界面。如果您希望在存在 dist 的情况下仍然使用简单页面，可以设置 `FINAINCE_PACKAGED_SPA=1`。

---

## 十五分钟快速上手

本节将引导您使用仓库自带的示例数据完成一套完整的因子管理流程。整个示例基于系统内置的测试数据集 `local_panel`（包含两只股票），可以让您在短时间内了解 FinAlpha 的核心功能。

> **注意**：以下每一条都是真实可用的 CLI 命令。[产品手册](docs/handbook.md)中沿用这条路径提供了详细的操作截图和步骤说明，建议结合手册一起学习。

### 步骤 1：系统检查
```bash
finaince doctor
```
验证系统环境和依赖是否正确配置。

### 步骤 2：运行基准测试
```bash
finaince baseline
```
运行基准测试，验证系统的核心计算功能是否正常。

### 步骤 3：因子表达式评测
```bash
# 使用 repro_polars 方言
finaince eval "Rank(Delta(close, 1))" --dialect repro_polars --backend local

# 使用 qlib 方言
finaince eval 'Rank($close)' --dialect qlib --backend local
```
在本地后端上对指定的因子表达式进行评测，支持多种方言。

### 步骤 4：实现自定义因子
```bash
finaince impl examples/15min/compute.py --name rank_delta --universe local_panel
```
将示例代码中的因子实现（`examples/15min/compute.py`）注册到系统中，指定因子名称和适用的股票范围（`local_panel`）。

### 步骤 5：提升因子等级
```bash
finaince promote '<catalog_id>' --to to_pool --yes
```
将指定的因子从目录中提升到因子池（pool），`--yes` 参数表示跳过交互式确认。

### 步骤 6：审查因子
```bash
finaince review
```
进入因子审查界面，查看待审核的因子列表。

### 步骤 7：批准因子加入池
```bash
finaince review --approve '<promotion_id>' --override thin_panel
```
批准指定的因子晋升请求。对于本示例中仅包含两只股票的测试数据集，必须使用 `--override thin_panel` 参数来覆盖系统的股票数量检查。

### 步骤 8：启动服务
```bash
finaince serve --host 127.0.0.1 --port 8000
```
启动 FinAlpha 的 Web 服务，默认在本地 8000 端口监听。

### 重要说明

> **HTTP API 限制**：通过 `POST /api/v1/review/{id}/approve` 接口提交批准请求时，如果请求体中包含 `{"override":[...]}` 参数，系统会固定返回 403 状态码。这是出于安全考虑的设计，防止通过 API 绕过系统的安全检查。对于示例中的测试数据集，请使用上述 CLI 命令中的 `--override thin_panel` 参数来完成批准操作。

> **晋升失败的常见原因**：
> - 股票数量过少（少于 20 只）：系统会因 `thin_panel` 检测而暂停晋升流程
> - 使用了公式代理：系统检测到因子实现使用了代理公式时会拒绝晋升
> - 缺少关键指标：如果因子缺少 IC（信息系数）或收益数据，晋升会停留在待审状态或直接失败

---

## Baseline 基准测试

`finaince baseline` 命令（以及对应的 Python 函数 `run_locked_baseline()`）用于运行系统的基准测试。该测试使用一个固定的配置，确保在不同环境下都能获得一致的结果，从而验证系统的计算准确性和稳定性。

### 测试配置

| 配置项 | 值 | 说明 |
|---|---|---|
| 测试窗口 | 2023-01-03 至 2023-02-10 | 固定的历史数据区间 |
| 股票范围（universe） | `local_panel` | 系统自带的包含两只股票的测试数据集 |
| 交易成本 | 0 bps | 无交易成本设定 |
| 因子表达式 | `Rank(Delta(close, 1))` | 基于收盘价变化的排序因子 |
| 方言 | `repro_polars` | 使用 Polars 作为后端计算引擎 |

### 结果验证

基准测试的一个重要特性是**可重复性**。连续运行两次时，输出的 `ok` 状态以及 IC / Sharpe 比率等关键指标必须完全一致。这验证了系统计算的稳定性和确定性。

> **数据来源声明**：在对外发布或分享任何基于 FinAlpha 的数据时，请务必明确注明：数据来自系统内置的 `local_panel` 测试数据集，且交易成本为 0 bps。这可以避免他人误解数据的真实来源和计算条件。

> **股票数量限制**：当股票数量少于 20 只时，因子晋升流程会因为触发 `thin_panel`（稀疏面板）检测而自动暂停。这是系统的安全机制，用于防止在数据量不足的情况下得出可能不准确的结论。

## 真宇宙数字 (Comparable numbers)

要在完整的股票池上进行评测，而不是使用系统自带的测试数据集，请将环境变量 `FINAINCE_PANEL_PATH` 指向一个符合 `prices.parquet` 格式（包含 `trade_date`、`ts_code`、`close` 等列）的 Parquet 文件。或者，配置 RiceQuant 凭证（`RQ_USER` 和 `RQ_PASS`）以解锁 `data_backend=ricequant`。所有晋升的因子指标都应明确标注交易成本（如 `cost_bps`）、股票池范围和回测窗口，以确保数据的可比性。

---

## 功能特性

FinAlpha 提供了一套完整的因子管理和研报复现解决方案，具有以下核心功能特性：

### 全流程因子管理
- **目录管理（catalog）**：将选股因子统一收录到可搜索、可分类的目录中，方便后续检索和管理
- **系统评测（eval）**：在本地数据面板上对因子进行历史回测和性能评估
- **门禁检测**：对因子进行一系列的质量检查，确保其满足基本的标准
- **晋升审核**：通过审核的因子可以晋升到更高的等级，进入因子池供后续使用

### 研报复现
- 使用 `reproagent` 工具复现卖方研报中的研究内容
- 使用 `finpdfpro` 工具准确还原研报的版面布局
- 支持研报中数学公式的解析和计算
- 实现研报结果的可验证性和可重现性

### 灵活的测试环境
- **内置测试数据**：系统自带 `local_panel`，包含精心挑选的两只股票，用于快速验证和演示
- **零交易成本**：测试环境中的交易成本设定为 0 bps，排除了交易成本对因子评测的影响
- **固定窗口测试**：使用固定的历史数据窗口，确保测试结果的可重复性

### 多方言支持
- 支持 `repro_polars` 方言：基于 Polars 的高效计算引擎
- 支持 `qlib` 方言：与 Qlib 框架的深度集成
- 在 Python 3.12 slim 环境中，通过进程内 local child 方式运行 `qlib` 方言，实现了轻量级的环境部署

### 研究环与晋升纪律
- **成本感知评测**：`cost_bps` 净值口径（换手来自回测明细），基准支持含成本双跑
- **多重检验门禁**：`weak_ic`（Harvey-Liu t ≥ 3）与 `inflated_sharpe`（Deflated Sharpe），搜索幸存者偏差不再混进晋升
- **生成端正则化**：AST 结构查重门禁 `homogeneous` 与复杂度上限 `overcomplex`；候选入池前近重复直接淘汰；catalog 带 `expr_hash` O(1) 查重
- **吞吐优化**：进程级本地 panel 缓存、在途作业查重守卫、`FINAINCE_MAX_JOBS` 并发上限、显著负 IC 因子的符号镜像
- **真宇宙数字轨**：`finaince bench` 输出 CSI300 point-in-time 双窗基准表（IS 2019–2023 / OOS 2024，双边 5bps），缓存带 sha256 清单，数字可第三方复现
- **沙箱分层**：冻结内建之上可选 bubblewrap 层（`--new-session --unshare-all`），结果诚实标注实际层，失败自动回落
- **治理内代码因子进化**：LLM 写完整 `compute(panel)` 进沙箱，失败按错误前缀 + AST 差异改写下一稿，产物全过门禁
- **动态组合**：跨 ready 因子滚动逆波动率每日权重（严格无未来信息），换手计成本；loop 奖励升级为净 Sharpe
- **BRAIN 外部裁决**：治理流产出可提交平台评级并回写 catalog；无凭据时声明降级为内部双窗基准
- **语料战役**：券商研报批量治理内复现，断点续跑，`no_factors` 是诚实终态
- **过程记忆**：以「门禁+对抗存活」做信用分配，AST 差异编辑动机加权记忆注入 advisor；经验链降为展示层
- **研究循环**：因子/模型交替，表达式队列批量无人值守；模型头可切换 OLS/LightGBM（walk-forward，库缺失诚实跳过）；可选 LLM advisor，失败回落启发式
- **对抗评审**：批准前可在全新子进程里重跑同一条评测并比对指标容差，数值对不上就否决
- **教练层**：低相关样例采样 + 失败教训蒸馏（`research_context`），回灌到提案与 advisor
- **事件链**：每步带 hypothesis 的 trace 链，按错误前缀检索同类失败

### 与同类工具的对比

| 工具 | 运行方式 | 数据来源 | 使用场景 |
|---|---|---|---|
| RD-Agent | 整夜自动运行 Qlib CSI300 | 公开的 CSI300 数据 | 批量自动化测试 |
| FinAlpha | 人工审查 + 自动化检测 | 内置 `local_panel` + 自定义数据 | 精细化因子管理 |

与 RD-Agent 等自动化工具不同，FinAlpha 更强调**用户的主观能动性**。系统不会自动运行所有因子，而是提供工具让您：

1. **自主审查目录**：您可以自由浏览、筛选和管理因子目录
2. **验证研报内容**：您可以复现并验证卖方研报中的因子和结论
3. **明确数据来源**：在发布任何数据时，您必须明确标注数据来源于 `local_panel`、交易成本为 0 bps

这种设计理念使得 FinAlpha 更适合需要对因子进行精细化管理和深度分析的专业用户。

> **关于 Qlib 的说明**：Qlib 框架公开发布的数据基于长窗口的 CSI300 指数成分股。而 FinAlpha 发布的测试数据则基于固定的短窗口。两者的数据周期和股票范围都有所不同，请在使用时注意区分。
>
> 全景竞品对比见 [docs/competitor-analysis.md](docs/competitor-analysis.md)，路线图见 [docs/improvement-plan-v3.md](docs/improvement-plan-v3.md)，逐步实现记录见 [docs/CHANGELOG.md](docs/CHANGELOG.md)。

---

## 测试

FinAlpha 提供了完善的测试体系，确保系统功能的稳定性和可靠性。

### 单元测试

运行以下命令可以执行系统的大部分单元测试：

```bash
python -m pytest tests --ignore=tests/test_live_real.py
```

这些测试涵盖了系统的核心功能，包括因子计算、数据处理、API 接口等。

### 可选的集成测试

系统还提供了一部分集成测试，用于验证与外部服务的交互：

```bash
pytest -m live
```

这些测试包括：
- **券商 PDF**：测试从券商获取研报 PDF 的功能
- **米筐**：测试与米筐数据服务的集成
- **聊天模型**：测试与大语言模型的交互

> **重要提醒**：这部分测试需要相应的 API 凭证才能正常运行。如果您没有配置相关的 API 密钥或访问权限，请**不要**运行这些测试，否则会导致测试失败。

---

## 参与贡献

FinAlpha 是一个开源项目，我们欢迎并鼓励社区的参与和贡献。无论是代码贡献、文档改进、bug 报告还是功能建议，都是对项目发展的重要支持。

有关如何参与贡献的详细信息，请参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 文件。该文件包含了：
- 项目的开发环境搭建指南
- 代码提交的规范和流程
- Pull Request 的审查标准
- 社区行为准则

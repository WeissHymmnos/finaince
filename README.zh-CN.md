# FinAlpha

[English](README.md) · **中文**

FinAlpha 管卖方研报里的选股因子：收进 catalog，在本地 panel 上评测，过了门禁再晋升；研报用 `reproagent` 和 `finpdfpro` 复现。

安装包名是 `finaince`，产品名是 **FinAlpha**。

**[产品手册](docs/handbook.md)**：完整教程、命令说明、HTTP 约定，以及工作台截图。

![填好 desk token 后的 Catalog](docs/handbook/images/01-catalog.png)

安装冒烟数字来自 `finaince baseline`：universe 为 `local_panel`，成本 0 bps，表达式为 `Rank(Delta(close, 1))`。这是两只股票的夹具。

许可证：[GNU Affero General Public License v3.0](LICENSE)。版权和第三方引擎见 [NOTICE](NOTICE)。

## 公开安装（Python 3.12）

只需要这一个公开仓库。引擎从 GitHub 上钉死的 commit 安装，见 `pyproject.toml` 的 extras。除非设置 `FINAINCE_PATH_HACK=1`，否则不会注入作者机器上的旁路源码。

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
cp .env.example .env   # 先写上 FINAINCE_DESK_TOKEN，再跑 finaince serve
finaince doctor
```

`doctor` 只有在 JSON 的 `ok` 为 true 时才退出 0。装完后 `import finaince, reproagent` 以及 `from aiminer.manager import cull_alpha_pool` 都要成功。读 catalog / review，以及所有写操作 HTTP，都需要 `FINAINCE_DESK_TOKEN`。默认绑定 `127.0.0.1:8000`。绑到 `0.0.0.0` 需要 `FINAINCE_ALLOW_PUBLIC_BIND=1`。

可选 extras：`.[agent]`（Claude Agent SDK）、`.[dev]`（pytest）、`.[all]`。

wheel 里带的是占位工作台（`finaince/web/index.html`）。完整的 Catalog / Review 界面需要编好的 `aiminer/frontend/dist`。旁边已有 dist 时，设 `FINAINCE_PACKAGED_SPA=1` 仍可强制用占位页。

## 十五分钟路径（仓库内夹具）

用的是随包装好的薄 `local_panel`。下面每条都是真实 CLI。[手册](docs/handbook.md) 按同一条路径配了截图。

```bash
finaince doctor
finaince baseline
finaince eval "Rank(Delta(close, 1))" --dialect repro_polars --backend local
finaince eval 'Rank($close)' --dialect qlib --backend local
finaince impl examples/15min/compute.py --name rank_delta --universe local_panel
finaince promote '<catalog_id>' --to to_pool --yes
finaince review
finaince review --approve '<promotion_id>' --override thin_panel
finaince serve --host 127.0.0.1 --port 8000
```

HTTP `POST /api/v1/review/{id}/approve` 若带上 `{"override":[...]}`，固定返回 403。冒烟面板要进 pool，请用上面的 CLI `--override thin_panel`。行偏薄、用了公式代理、或缺 IC / 收益时，晋升会停在待审或直接失败。

## 安装冒烟 baseline

`finaince baseline`（以及 `run_locked_baseline()`）把窗口锁在打包的两只股票 panel 上：

| 字段 | 值 |
|---|---|
| 窗口 | 2023-01-03 → 2023-02-10 |
| universe | `local_panel` |
| 成本 | 0 bps |
| 表达式 | `Rank(Delta(close, 1))` |
| 方言 | `repro_polars` |

连续跑两次，`ok` 以及报出的 IC / Sharpe 必须一致。对外声明里会写明这是 `local_panel` 冒烟夹具、成本 0 bps。股票数少于 20 时，晋升会因 `thin_panel` 停下。

## 能力

- 同一张台上完成 catalog → eval → 门禁晋升 / 审核
- 用 `reproagent` 和 `finpdfpro` 复现研报（版面和公式）
- 在随包装好的 `local_panel` 上跑锁定冒烟窗口（0 bps）
- 3.12 slim extra 上通过进程内 local child 跑 `qlib` 方言

和邻近工具比：RD-Agent 通宵自己跑 Qlib CSI300；FinAlpha 让你自己审目录和研报，并写明冒烟窗口用的是 `local_panel`、成本 0 bps。Qlib 公开数字来自长窗口 CSI300；这里发表的是这份锁定冒烟窗口。

## 测试

```bash
python -m pytest tests --ignore=tests/test_live_real.py
```

券商 PDF、米筐、聊天模型的测试是可选的（`pytest -m live`），没有凭据就不要跑。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

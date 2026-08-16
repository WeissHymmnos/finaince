# FinAlpha

**English** · [中文](README.zh-CN.md)

Fail-closed research desk for **China sell-side factor work**: catalog a candidate, evaluate it on a locked local panel, promote it only when the gates pass, and reproduce a 研报 through `reproagent` + `finpdfpro`.

The installable package name is `finaince`. The product name is **FinAlpha**.

**[Product handbook](docs/handbook.md)** — full tutorial (Chinese), CLI reference, HTTP contract, and workbench screenshots.

![Catalog with a desk token](docs/handbook/images/01-catalog.png)

`finaince baseline` runs the two stocks shipped as `local_panel`: cost 0 bps, expression `Rank(Delta(close, 1))`. Those numbers only show that the install ran.

License: [GNU Affero General Public License v3.0](LICENSE). See [NOTICE](NOTICE) for copyright and third-party engines.

## Public install (Python 3.12)

You only need this public repository. Engines resolve from public GitHub at pinned commits (see `pyproject.toml` extras). Sibling `src` injection stays off unless you set `FINAINCE_PATH_HACK=1`.

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
cp .env.example .env   # set FINAINCE_DESK_TOKEN before `finaince serve`
finaince doctor
```

`doctor` exits 0 only when its JSON `ok` is true. `import finaince, reproagent` and `from aiminer.manager import cull_alpha_pool` must work after this install. Catalog/review reads and mutation HTTP require `FINAINCE_DESK_TOKEN`. Bind default is `127.0.0.1:8000`. Binding `0.0.0.0` needs `FINAINCE_ALLOW_PUBLIC_BIND=1`.

Optional extras: `.[agent]` (Claude Agent SDK), `.[dev]` (pytest), `.[all]`.

The wheel ships a stub workbench (`finaince/web/index.html`). A full Catalog/Review UI needs a built `aiminer/frontend/dist`. `FINAINCE_PACKAGED_SPA=1` forces the stub when a sibling dist is present.

## 15-minute path (shipped example)

Uses the two-stock `local_panel` parquet in the package. Every command is a real CLI entry. The [handbook](docs/handbook.md) walks the same path with screenshots.

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

HTTP `POST /api/v1/review/{id}/approve` with `{"override":[...]}` stays 403. To put these two stocks in the pool, use the CLI `--override thin_panel` line above. Promotion stays pending or fail-closed when the row is thin, proxied, or missing IC/returns.

## baseline

`finaince baseline` (and `run_locked_baseline()`) lock a short window on the two stocks shipped in the package:

| Field | Value |
|---|---|
| window | 2023-01-03 → 2023-02-10 |
| universe | `local_panel` |
| cost | 0 bps |
| expression | `Rank(Delta(close, 1))` |
| dialect | `repro_polars` |

Two consecutive runs must agree on `ok` and the reported IC/Sharpe. When you quote those numbers, say they come from the shipped `local_panel` at 0 bps. Promote/approve fail-closed with `thin_panel` while the loaded panel has fewer than 20 assets.

## Features

- Catalog → eval → fail-closed promote/review on one desk
- 研报复现 via `reproagent` and `finpdfpro` (layout and formulas)
- Fixed-window eval on the two stocks shipped as `local_panel` (0 bps)
- `qlib` dialect on the 3.12 slim extra via the in-process local child

Compared with nearby stacks: RD-Agent is an unattended Qlib CSI300 R&D loop; FinAlpha is a human review desk with Chinese sell-side PDF fidelity, and it says so when numbers come from the shipped `local_panel` at 0 bps. Qlib's public figures use long CSI300 windows; FinAlpha publishes this short locked window.

## Tests

```bash
python -m pytest tests --ignore=tests/test_live_real.py
```

Live PDF / RiceQuant / chat-LLM tests stay opt-in (`pytest -m live`) and are skipped without credentials.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md).

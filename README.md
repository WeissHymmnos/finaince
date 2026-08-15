# FinAlpha

Fail-closed research desk for **China sell-side factor work**: catalog a candidate, evaluate it on a locked local panel, promote it only when the gates pass, and reproduce a 研报 through `reproagent` + `finpdfpro`.

The installable package name is `finaince`. The product name is **FinAlpha**.

The public research figure is the locked local-panel baseline (`finaince baseline`): universe `local_panel`, cost 0 bps, expression `Rank(Delta(close, 1))`.

License: [GNU Affero General Public License v3.0](LICENSE).

## Public install (Python 3.12)

You only need this public repository. Engines resolve from public GitHub (`WeissHymmnos/ReproAgent` @ `finaince-desk`, `WeissHymmnos/aiminer` @ `finaince-312`). No private token, no author `Documents/` layout, no `../reproagent` checkout.

```bash
git clone https://github.com/WeissHymmnos/finaince.git
cd finaince
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction]"
finaince doctor
```

`doctor` exits 0 only when its JSON `ok` is true. `import finaince, reproagent` and `from aiminer.manager import cull_alpha_pool` must work after this install (CI sets `FINAINCE_NO_PATH_HACK=1` so the path fallback is off).

Optional extras:

- `.[agent]` — Claude Agent SDK research desk
- `.[dev]` — pytest
- `.[all]` — reproduction + discovery + agent

Bind default for `finaince serve` is `127.0.0.1:8000`.

## 15-minute path (in-repo fixture)

Uses the shipped thin `local_panel` parquet. Every command below is a real CLI entry.

```bash
# 1. Health
finaince doctor

# 2. Public research number (locked window / universe / cost / expression)
finaince baseline

# 3. Same expression through the eval router
finaince eval "Rank(Delta(close, 1))" --dialect repro_polars --backend local

# 4. Isolated compute(panel) on the fixture → catalog row
finaince impl examples/15min/compute.py --name rank_delta --universe local_panel

# 5. Promote → review (fail-closed: thin_panel / formula_proxy / missing IC / missing returns)
#    Copy catalog_id from the impl JSON, then:
finaince promote '<catalog_id>' --to to_pool --yes
finaince review
```

Promotion stays pending or fail-closed when the row is thin, proxied, or missing IC/returns. `qlib` on the 3.12 slim install reports `ok: false` until a real 3.10 subprocess is enabled.

## Public research number

`finaince baseline` (and `run_locked_baseline()`) lock:

| Field | Value |
|---|---|
| window | 2023-01-03 → 2023-02-10 |
| universe | `local_panel` |
| cost | 0 bps |
| expression | `Rank(Delta(close, 1))` |
| dialect | `repro_polars` |

Two consecutive runs must agree on `ok` and the reported IC/Sharpe (or the same skip). The claim names the `local_panel` fixture and 0 bps cost.

## Features

- Catalog → eval → fail-closed promote/review on one desk
- 研报复现 via `reproagent` and `finpdfpro` (layout and formulas)
- Citable locked-window number on the shipped `local_panel`

Compared with nearby stacks: RD-Agent is an unattended Qlib CSI300 R&D loop; FinAlpha is a human review desk with Chinese sell-side PDF fidelity and a declared local-panel baseline. Qlib's public figures use long CSI300 windows; FinAlpha publishes the locked `local_panel` window and 0 bps cost.

## Tests

```bash
python -m pytest tests --ignore=tests/test_live_real.py
```

Live PDF / RiceQuant / CPA DeepSeek tests stay opt-in (`pytest -m live`) and are skipped without credentials.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md).

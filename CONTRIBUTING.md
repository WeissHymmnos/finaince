# Contributing to FinAlpha

Repository: <https://github.com/WeissHymmnos/finaince>  
Product handbook: [docs/handbook.md](docs/handbook.md)

## Development Setup

Install from a clean virtual environment using the public Git dependencies declared in `pyproject.toml`:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[reproduction,dev]"
```

## Core Invariants

Every pull request must preserve the following contracts:

1. **Hermetic public installation.** `uv pip install -e ".[reproduction]"` must succeed on a clean machine without private tokens or sibling directory checkouts (`../reproagent`, `../aiminer`). GitHub Actions workflows must pin the exact commit SHAs listed in `pyproject.toml`.
2. **Fail-closed promotion gates.** `unprovenanced` and `not_citable` cannot be overridden. Panels with fewer than 20 stocks must trigger `thin_panel`; placeholder formulas must trigger `formula_proxy`; missing IC or daily return series must block promotion. Over HTTP, `POST /api/v1/review/{id}/approve` must reject any `override` payload with HTTP `403` and always run adversarial subprocess verification.
3. **Deterministic install probe.** `finaince baseline` runs on the packaged two-stock `local_panel` fixture (`2023-01-03` → `2023-02-10`, `0 bps`, `Rank(Delta(close, 1))`) and must produce identical metrics across consecutive runs.
4. **Loopback-first server defaults.** `finaince serve` binds to `127.0.0.1` by default and requires `FINAINCE_DESK_TOKEN` on all `/api/v1` endpoints except `/api/v1/health` and `/api/v1/baseline`.

## Required Pre-PR Checks

Both commands below run in CI (`pytest-offline` and `packaging-312`) and must pass locally before opening a PR:

```bash
python -m pytest tests --ignore=tests/test_live_real.py
FINAINCE_NO_PATH_HACK=1 python -c "import finaince, reproagent; from aiminer.manager import cull_alpha_pool; print('ok', cull_alpha_pool)"
```

 Also run Ruff to verify formatting and lint rules:

```bash
uv run ruff check src/ tests/
```

## Pull Request Guidelines

- Keep each PR focused on a single concern.
- Test through the shipped CLI entry points or public module functions rather than re-implementing logic inside the test suite.
- If you modify installation instructions, packaging metadata, or CI workflows, update `tests/test_public_research.py` and `tests/test_packaging_flag.py` to keep the public-install contract covered.

## License

By contributing to FinAlpha, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0](LICENSE).

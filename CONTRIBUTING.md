# Contributing to FinAlpha

Public repository: https://github.com/WeissHymmnos/finaince

## Before a PR

1. Fork or branch from `main`.
2. Install as a stranger would:

   ```bash
   uv venv --python 3.12 .venv
   source .venv/bin/activate
   uv pip install -e ".[reproduction,dev]"
   ```

3. Keep the documented install on public Git extras (`uv pip install -e ".[reproduction]"`). Checkout tokens and author-machine `../reproagent` paths stay out of docs and workflows.
4. Keep promotion fail-closed: `thin_panel`, `formula_proxy`, missing IC, and missing returns still block a thin panel from a broad-universe claim.
5. The public research number is `finaince baseline` on `local_panel` (locked window, 0 bps).

## Checks that must pass

```bash
python -m pytest tests --ignore=tests/test_live_real.py
FINAINCE_NO_PATH_HACK=1 python -c "import finaince, reproagent; from aiminer.manager import cull_alpha_pool; print('ok', cull_alpha_pool)"
```

GitHub Actions runs both of those on `main` (`pytest-offline` and `packaging-312`). A PR that makes either job need a private secret will be rejected.

## PR shape

- One concern per PR.
- Drive new behavior through a shipped function or CLI, not a re-implemented helper inside the test.
- If you touch install docs or workflows, update `tests/test_public_research.py` (or `tests/test_packaging_flag.py`) so the public-install contract stays asserted.

## License

Contributions are accepted under AGPL-3.0 (`LICENSE`).

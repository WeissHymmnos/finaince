"""Adversarial stress harness and empirical challenge suite for Phase 2 R3 & R4.

Author: Challenger P2-2
Purpose:
- R3: Adversarial AST Sandbox Escape exploits & Zero False Positives validation.
- R4: Extreme panel benchmark (1250 days x 300 symbols) and edge case validation.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from finaince.isolate import (
    child_isolate,
    validate_factor_ast,
)

# ==============================================================================
# R3: Sandbox Attack Payloads & Adversarial Exploits
# ==============================================================================


class TestR3AdversarialAttacks:
    """Empirical challenge suite testing attack payloads against isolate.py."""

    def test_exploit_01_standard_ast_attacks_intercepted(self) -> None:
        """Verify baseline AST attacks fail-closed with ASTSecurityError or ImportError."""
        standard_attacks = [
            # Object tree crawling
            (
                "object_tree_mro",
                "def compute(panel): return ().__class__.__mro__[1].__subclasses__()",
            ),
            (
                "object_tree_bases",
                "def compute(panel): return [].__class__.__bases__[0].__subclasses__()",
            ),
            # Direct dynamic execution
            ("eval_direct", "def compute(panel): return eval('[1, 2, 3]')"),
            ("exec_direct", "def compute(panel): exec('a = 1'); return [1, 2, 3]"),
            ("compile_direct", "def compute(panel): compile('1', '', 'eval'); return [1, 2, 3]"),
            ("open_direct", "def compute(panel): open('/etc/passwd'); return [1, 2, 3]"),
            # Direct reflection
            ("getattr_direct", "def compute(panel): getattr(panel, 'close'); return [1, 2, 3]"),
            ("setattr_direct", "def compute(panel): setattr(panel, 'x', 1); return [1, 2, 3]"),
            ("delattr_direct", "def compute(panel): delattr(panel, 'close'); return [1, 2, 3]"),
            ("hasattr_direct", "def compute(panel): hasattr(panel, 'close'); return [1, 2, 3]"),
            # Direct imports
            ("import_os", "import os\ndef compute(panel): return [1, 2, 3]"),
            ("import_socket", "import socket\ndef compute(panel): return [1, 2, 3]"),
            ("from_os_system", "from os import system\ndef compute(panel): return [1, 2, 3]"),
            ("from_subprocess", "from subprocess import Popen\ndef compute(panel): return [1, 2, 3]"),
            # Whitespace evasions
            ("eval_whitespace", "def compute(panel):\n    eval   ('1+1')\n    return [1, 2, 3]"),
            ("open_whitespace", "def compute(panel):\n    open  ('/etc/passwd')\n    return [1, 2, 3]"),
            # Dunder key in constant subscript
            ("subscript_dunder_constant", "def compute(panel): return panel['__class__']"),
        ]

        for label, payload in standard_attacks:
            res = child_isolate({"source": payload, "name": label})
            assert res["ok"] is False, f"Exploit '{label}' succeeded! Expected fail-closed."
            err = str(res.get("error", ""))
            assert any(
                token in err.lower()
                for token in ("astsecurityerror", "importerror", "permissionerror")
            ), f"Exploit '{label}' did not fail with ASTSecurityError or ImportError: {err}"

    def test_exploit_02_operator_attrgetter_transitive_rce_escape(self) -> None:
        """CRITICAL VULNERABILITY AUDIT:

        Tests whether operator.attrgetter can be chained to retrieve json.loads.__globals__,
        access unrestricted __builtins__, and execute arbitrary code.
        """
        # Exploit payload bypassing AST visitor via operator.attrgetter
        rce_payload = """
def compute(panel):
    g = operator.attrgetter('__globals__')(json.loads)
    b = g.get('__builtins__')
    imp = b.get('__import__')
    os_mod = imp('os')
    pid = float(os_mod.getpid())
    return [pid, 2.0, 3.0]
"""
        res = child_isolate({"source": rce_payload, "name": "attrgetter_rce"})
        # A secure sandbox MUST reject this attack!
        # If ok is True, the sandbox has been breached.
        is_breached = res["ok"] is True
        assert not is_breached, (
            "SECURITY VULNERABILITY CONFIRMED: Sandbox escaped via operator.attrgetter! "
            f"Result: {res}"
        )

    def test_exploit_03_implicit_io_escape_pandas_polars_read_csv(self) -> None:
        """CRITICAL VULNERABILITY AUDIT:

        Tests whether pre-injected pd / pl can read arbitrary local files (e.g. /etc/passwd)
        without being intercepted.
        """
        io_payload = """
def compute(panel):
    df = pl.read_csv('/etc/passwd')
    return [1.0, 2.0, 3.0]
"""
        res = child_isolate({"source": io_payload, "name": "pl_read_csv"})
        is_breached = res["ok"] is True
        assert not is_breached, (
            "SECURITY VULNERABILITY CONFIRMED: Sandbox allows implicit file I/O via pl.read_csv! "
            f"Result: {res}"
        )

    def test_exploit_04_dynamic_subscript_dunder_bypass(self) -> None:
        """VULNERABILITY AUDIT:

        Tests whether dynamic expression in subscript slice bypasses constant slice check.
        """
        dynamic_subscript_payload = """
def compute(panel):
    g = operator.attrgetter('__globals__')(json.loads)
    k = '__' + 'builtins__'
    b = g[k]
    return [1.0, 2.0, 3.0]
"""
        res = child_isolate({"source": dynamic_subscript_payload, "name": "dynamic_subscript"})
        is_breached = res["ok"] is True
        assert not is_breached, (
            "SECURITY VULNERABILITY CONFIRMED: Dynamic dunder subscript slice allowed! "
            f"Result: {res}"
        )


# ==============================================================================
# R3: Zero False Positives Verification (Valid Advanced Quant Factors)
# ==============================================================================


class TestR3ZeroFalsePositives:
    """Verify that 100% of legitimate, advanced quant factors execute cleanly."""

    def test_valid_polars_advanced_rolling_expressions(self) -> None:
        """Verify advanced Polars rolling z-score and when-then-otherwise factor."""
        code = """
def compute(panel):
    df = pl.DataFrame(panel)
    factor_expr = (
        (pl.col("close") - pl.col("close").rolling_mean(3))
        / (pl.col("close").rolling_std(3) + 1e-6)
    )
    res = df.select(
        pl.when(factor_expr.is_not_null())
        .then(factor_expr)
        .otherwise(0.0)
        .alias("score")
    )
    return res["score"].to_list()
"""
        validate_factor_ast(code)
        res = child_isolate({"source": code, "name": "polars_rolling_zscore"})
        assert res["ok"] is True, f"Valid factor failed: {res.get('error')}"
        assert len(res["values"]) >= 3

    def test_valid_pandas_advanced_rolling_metrics(self) -> None:
        """Verify advanced Pandas rolling returns, ewm and rank factor."""
        code = """
def compute(panel):
    s = pd.Series(panel["close"])
    pct = s.pct_change()
    ewm = pct.ewm(span=3).mean()
    ranked = ewm.rank(pct=True).fillna(0.0)
    return ranked.tolist()
"""
        validate_factor_ast(code)
        res = child_isolate({"source": code, "name": "pandas_ewm_rank"})
        assert res["ok"] is True, f"Valid factor failed: {res.get('error')}"
        assert len(res["values"]) >= 3

    def test_valid_numpy_linear_algebra_factor(self) -> None:
        """Verify advanced NumPy linear regression slope and convolve weights factor."""
        code = """
def compute(panel):
    c = np.array(panel["close"], dtype=float)
    x = np.arange(len(c), dtype=float)
    # Covariance slope
    cov = np.cov(x, c)[0, 1]
    var_x = np.var(x)
    slope = cov / (var_x + 1e-12)
    # Moving convolve
    weights = np.array([0.2, 0.3, 0.5])
    conv = np.convolve(c, weights, mode='same')
    return list(conv * slope)
"""
        validate_factor_ast(code)
        res = child_isolate({"source": code, "name": "numpy_linalg_factor"})
        assert res["ok"] is True, f"Valid factor failed: {res.get('error')}"
        assert len(res["values"]) >= 3

    def test_valid_math_statistics_factor(self) -> None:
        """Verify standard math and statistics quant operations."""
        code = """
def compute(panel):
    c = list(panel["close"])
    med = statistics.median(c)
    dev = statistics.stdev(c) if len(c) > 1 else 1.0
    out = [math.erf((x - med) / (dev + 1e-6)) for x in c]
    return out
"""
        validate_factor_ast(code)
        res = child_isolate({"source": code, "name": "math_stats_factor"})
        assert res["ok"] is True, f"Valid factor failed: {res.get('error')}"
        assert len(res["values"]) >= 3


# ==============================================================================
# R4: Vectorization Performance & Extreme Load Benchmark
# ==============================================================================


class TestR4VectorizationExtremeBenchmark:
    """Stress-test run_bench under 1250 trading days and 300+ symbols."""

    def test_run_bench_extreme_panel_strictly_under_1_second(self, isolated_home: Path) -> None:
        """BENCHMARK CHALLENGE:

        Execute run_bench with 1250 trading days and 300 assets.
        Target: strictly < 1.0s.
        """
        from finaince.data_track import run_bench
        from tests.test_data_track import _seed_cache

        # 5 years (2020-2024 ~ 1250 trading days) with 300 assets (~386,000 panel rows)
        _seed_cache(isolated_home, years=tuple(range(2020, 2025)), n_assets=300)

        t0 = time.perf_counter()
        res = run_bench(
            is_start="2020-01-01",
            is_end="2023-12-31",
            oos_start="2024-01-01",
            oos_end="2024-12-31",
            cost_bps=5.0,
        )
        elapsed = time.perf_counter() - t0

        assert res.get("ok") is True, f"run_bench failed: {res.get('error')}"
        assert elapsed < 1.0, (
            f"PERFORMANCE CRITERION FAILED: run_bench took {elapsed:.4f}s "
            "on 1250 days x 300 assets (must be strictly < 1.0s). "
            "Redundant recomputation of _factor_artifacts detected."
        )


# ==============================================================================
# R4: Vectorization Edge Cases & Numerical Stability
# ==============================================================================


class TestR4VectorizationEdgeCases:
    """Test boundary conditions, degenerate distributions, and numerical stability."""

    def test_edge_zero_variance_factor(self) -> None:
        """Zero-variance factor: all scores identical. Must not divide by zero."""
        from finaince.data_track import _daily_ic, _layered_long_short

        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(25)]
        codes = [f"00000{i:02d}.SZ" for i in range(25)]
        u = {d: set(codes) for d in dates}

        scores_const = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_score": 5.0}
            for d in dates
            for c in codes
        ])
        fwd = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_fwd_ret": float((i % 7) - 3) * 0.005}
            for d in dates
            for i, c in enumerate(codes)
        ])

        ic = _daily_ic(scores_const, fwd, u, spearman=False)
        rank_ic = _daily_ic(scores_const, fwd, u, spearman=True)
        ls, _to = _layered_long_short(scores_const, fwd, u)

        # Zero variance means standard deviation is 0, so filtered out of IC calculation
        assert ic == []
        assert rank_ic == []
        assert len(ls) == 25

    def test_edge_single_stock_universe(self) -> None:
        """Single-stock universe: below minimum threshold of 10 for IC, 20 for LS."""
        from finaince.data_track import _daily_ic, _layered_long_short

        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(20)]
        codes = ["000001.SZ"]
        u = {d: set(codes) for d in dates}

        scores = pl.DataFrame([{"trade_date": d, "ts_code": "000001.SZ", "_score": 1.0} for d in dates])
        fwd = pl.DataFrame([{"trade_date": d, "ts_code": "000001.SZ", "_fwd_ret": 0.01} for d in dates])

        ic = _daily_ic(scores, fwd, u, spearman=False)
        ls, to = _layered_long_short(scores, fwd, u)

        assert ic == []
        assert ls == []
        assert to == {}

    def test_edge_sparse_suspensions_state_machine_reset(self) -> None:
        """Sparse suspensions: trading day active members drops below threshold."""
        from finaince.data_track import _layered_long_short

        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(30)]
        codes = [f"60000{i:02d}.SH" for i in range(25)]
        # Days 10 and 20 drop to 4 stocks
        u = {
            d: set(codes[:4]) if idx in (10, 20) else set(codes)
            for idx, d in enumerate(dates)
        }

        scores = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_score": float(i % 5)}
            for d in dates
            for i, c in enumerate(codes)
        ])
        fwd = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_fwd_ret": float((i % 5) - 2) * 0.01}
            for d in dates
            for i, c in enumerate(codes)
        ])

        ls, _turnover = _layered_long_short(scores, fwd, u)
        # Days 10 and 20 are skipped
        active_days = {d for d, _ in ls}
        assert dates[10] not in active_days
        assert dates[20] not in active_days
        assert len(ls) == 28

    def test_edge_all_nan_handling_and_recovery(self) -> None:
        """All-NaN returns: verify behavior when forward returns are NaN."""
        from finaince.data_track import _daily_ic, _layered_long_short, _metrics_from_artifacts

        dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(15)]
        codes = [f"00000{i:02d}.SZ" for i in range(25)]
        u = {d: set(codes) for d in dates}

        scores = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_score": float(i % 5)}
            for d in dates
            for i, c in enumerate(codes)
        ])
        fwd_nan = pl.DataFrame([
            {"trade_date": d, "ts_code": c, "_fwd_ret": float("nan")}
            for d in dates
            for i, c in enumerate(codes)
        ])

        ic = _daily_ic(scores, fwd_nan, u, spearman=False)
        ls, to = _layered_long_short(scores, fwd_nan, u)
        artifacts = {"ic_vals": ic, "rank_vals": ic, "ls": (ls, to)}
        metrics = _metrics_from_artifacts(artifacts, 5.0)

        # Metrics must report insufficient_days or None, not unhandled crash
        assert isinstance(metrics, dict)

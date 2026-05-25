"""``engine.robustness`` wrapper 单测。

Wrap 自 backtester-mcp，关注的是 **Inalpha 侧适配层正确性**——dataclass 字段映射、
输入校验、numpy/list 互转——而不是重复测 backtester-mcp 内部数学正确性
（那是上游的责任，他们的 CI 在跑 tests/test_robustness.py）。
"""
from __future__ import annotations

import numpy as np
import pytest

from inalpha_paper.engine.robustness import (
    BootstrapSharpeResult,
    DeflatedSharpeResult,
    PBOResult,
    bootstrap_sharpe_ci,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

# ──────────────────────────────────────────────────────────────────────
# probability_of_backtest_overfitting
# ──────────────────────────────────────────────────────────────────────


def _synth_returns(n_strategies: int, n_periods: int, seed: int) -> list[list[float]]:
    """N 个策略各 M 期 returns；高斯白噪声"""
    rng = np.random.default_rng(seed)
    return [rng.normal(0.0, 0.01, n_periods).tolist() for _ in range(n_strategies)]


def test_pbo_returns_dataclass_with_valid_fields() -> None:
    matrix = _synth_returns(n_strategies=6, n_periods=300, seed=1)
    result = probability_of_backtest_overfitting(matrix, n_splits=8)
    assert isinstance(result, PBOResult)
    assert 0.0 <= result.pbo <= 1.0
    assert result.n_combinations > 0
    # 白噪声 PBO 期望接近 0.5，留宽松边界（统计不稳定）
    assert 0.1 <= result.pbo <= 0.95


def test_pbo_too_few_strategies_raises() -> None:
    with pytest.raises(ValueError, match="至少需要 2 个策略"):
        probability_of_backtest_overfitting([[0.1, 0.2, 0.3]], n_splits=2)


def test_pbo_unequal_length_raises() -> None:
    matrix = [[0.1, 0.2, 0.3], [0.1, 0.2]]
    with pytest.raises(ValueError, match="必须等长"):
        probability_of_backtest_overfitting(matrix, n_splits=2)


def test_pbo_accepts_numpy_input() -> None:
    matrix = [list(np.random.default_rng(0).normal(0.0, 0.01, 200)) for _ in range(4)]
    result = probability_of_backtest_overfitting(matrix, n_splits=10)
    assert isinstance(result.pbo, float)


# ──────────────────────────────────────────────────────────────────────
# bootstrap_sharpe_ci
# ──────────────────────────────────────────────────────────────────────


def test_bootstrap_sharpe_positive_drift_excludes_zero() -> None:
    """有明显正漂移（mu/sigma 高）→ Sharpe 显著为正，CI 不应横跨 0。"""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.005, 0.005, 500).tolist()  # 高 SR
    result = bootstrap_sharpe_ci(returns, n_samples=500, seed=42)
    assert isinstance(result, BootstrapSharpeResult)
    assert result.sharpe > 0.0
    assert result.ci_includes_zero is False
    assert result.ci_lower <= result.sharpe <= result.ci_upper


def test_bootstrap_sharpe_zero_drift_includes_zero() -> None:
    """零漂移白噪声 → CI 应该跨 0。"""
    rng = np.random.default_rng(123)
    returns = rng.normal(0.0, 0.01, 500).tolist()
    result = bootstrap_sharpe_ci(returns, n_samples=500, seed=42)
    assert result.ci_includes_zero is True


def test_bootstrap_sharpe_short_series_raises() -> None:
    with pytest.raises(ValueError, match="长度必须 ≥ 2"):
        bootstrap_sharpe_ci([0.01], n_samples=100)


# ──────────────────────────────────────────────────────────────────────
# deflated_sharpe_ratio
# ──────────────────────────────────────────────────────────────────────


def test_deflated_sharpe_single_strategy_no_deflation() -> None:
    """n_strategies=1 → 无多重检验修正，DSR 退化为 t-test。"""
    result = deflated_sharpe_ratio(observed_sharpe=2.0, n_returns=252, n_strategies=1)
    assert isinstance(result, DeflatedSharpeResult)
    assert result.expected_max_sharpe == 0.0
    assert result.dsr > 0.0  # SR > 0 → DSR > 0


def test_deflated_sharpe_many_strategies_deflates() -> None:
    """试了 100 个策略挑最高，期望 max Sharpe 大幅抬高 → DSR 应该比 raw 小。"""
    result_few = deflated_sharpe_ratio(observed_sharpe=2.0, n_returns=252, n_strategies=2)
    result_many = deflated_sharpe_ratio(observed_sharpe=2.0, n_returns=252, n_strategies=100)
    # 选最优的"门槛"越高 → expected_max_sharpe 越大 → DSR 越小
    assert result_many.expected_max_sharpe > result_few.expected_max_sharpe
    assert result_many.dsr < result_few.dsr


def test_deflated_sharpe_invalid_args_raises() -> None:
    with pytest.raises(ValueError, match="n_strategies"):
        deflated_sharpe_ratio(observed_sharpe=1.0, n_returns=252, n_strategies=0)
    with pytest.raises(ValueError, match="n_returns"):
        deflated_sharpe_ratio(observed_sharpe=1.0, n_returns=1, n_strategies=5)


def test_deflated_sharpe_p_value_in_unit_interval() -> None:
    result = deflated_sharpe_ratio(observed_sharpe=1.5, n_returns=252, n_strategies=20)
    assert 0.0 <= result.p_value <= 1.0

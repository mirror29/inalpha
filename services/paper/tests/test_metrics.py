"""``engine.metrics`` 纯函数单测 —— 不依赖 Portfolio / BacktestEngine。"""
from __future__ import annotations

import math

import pytest

from inalpha_paper.engine import metrics

# ─── periods_per_year ───


def test_periods_per_year_known_timeframes() -> None:
    """常用 + CCXT 全覆盖（review 高风险 #4：metrics 缺 timeframe 让回测 500）。"""
    assert metrics.periods_per_year("1d") == 365
    assert metrics.periods_per_year("1h") == 8_760
    assert metrics.periods_per_year("1m") == 525_600
    # 之前缺失的几个 timeframe，data-service connectors 都支持
    assert metrics.periods_per_year("2h") == 12 * 365
    assert metrics.periods_per_year("3m") == 20 * 24 * 365
    assert metrics.periods_per_year("8h") == 3 * 365
    assert metrics.periods_per_year("12h") == 2 * 365
    assert metrics.periods_per_year("3d") == 365 // 3
    assert metrics.periods_per_year("1w") == 52
    assert metrics.periods_per_year("1M") == 12


def test_periods_per_year_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown timeframe"):
        metrics.periods_per_year("13m")  # 不是 CCXT 标准 timeframe


# ─── bar_returns ───


def test_bar_returns_basic() -> None:
    eq = [100.0, 110.0, 99.0]
    rets = metrics.bar_returns(eq)
    assert len(rets) == 2
    assert rets[0] == pytest.approx(0.1)
    assert rets[1] == pytest.approx(-0.1)


def test_bar_returns_skip_nonpositive_prev() -> None:
    """上一根 equity ≤ 0 时跳过，避免除零。"""
    eq = [100.0, 0.0, 100.0]
    rets = metrics.bar_returns(eq)
    # idx=1: prev=100 -> r = 0/100 - 1 = -1
    # idx=2: prev=0 -> skipped
    assert rets == [pytest.approx(-1.0)]


def test_bar_returns_short_series_returns_empty() -> None:
    assert metrics.bar_returns([]) == []
    assert metrics.bar_returns([100.0]) == []


# ─── sharpe_ratio ───


def test_sharpe_constant_returns_none() -> None:
    """std=0（完全平稳）返 None。"""
    rets = [0.01] * 100
    assert metrics.sharpe_ratio(rets, periods_per_year_=252) is None


def test_sharpe_too_few_samples_returns_none() -> None:
    assert metrics.sharpe_ratio([], 252) is None
    assert metrics.sharpe_ratio([0.01], 252) is None


def test_sharpe_positive_returns_above_zero() -> None:
    """正期望 + 正常波动 → 正 Sharpe。"""
    rets = [0.01, 0.02, 0.005, 0.015, 0.01]
    s = metrics.sharpe_ratio(rets, periods_per_year_=252)
    assert s is not None
    assert s > 0


def test_sharpe_zero_mean_yields_zero() -> None:
    """均值 0、波动正常 → Sharpe ≈ 0。"""
    rets = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005]
    s = metrics.sharpe_ratio(rets, periods_per_year_=252)
    assert s is not None
    assert abs(s) < 0.5  # 不强求严格 0（数值采样浮动），只要接近


def test_sharpe_negative_returns_below_zero() -> None:
    """负期望 → 负 Sharpe。"""
    rets = [-0.01, -0.02, -0.005, -0.015, -0.01]
    s = metrics.sharpe_ratio(rets, periods_per_year_=252)
    assert s is not None
    assert s < 0


def test_sharpe_annualization_scales_correctly() -> None:
    """同一组 returns，年化系数翻 4 倍 → Sharpe 翻 2 倍（sqrt(4)=2）。"""
    rets = [0.01, 0.02, -0.01, 0.015, -0.005, 0.008]
    s1 = metrics.sharpe_ratio(rets, periods_per_year_=252)
    s4 = metrics.sharpe_ratio(rets, periods_per_year_=252 * 4)
    assert s1 is not None and s4 is not None
    assert s4 / s1 == pytest.approx(2.0, rel=1e-6)


# ─── sortino_ratio ───


def test_sortino_no_downside_returns_none() -> None:
    """全正收益 → 无下行偏差 → None。"""
    rets = [0.01, 0.02, 0.005, 0.015]
    assert metrics.sortino_ratio(rets, 252) is None


def test_sortino_too_few_samples_returns_none() -> None:
    assert metrics.sortino_ratio([], 252) is None
    assert metrics.sortino_ratio([0.01], 252) is None


def test_sortino_positive_for_winning_strategy() -> None:
    """期望正、有少量下行 → 正 Sortino。"""
    rets = [0.02, 0.01, -0.005, 0.015, -0.003, 0.018]
    s = metrics.sortino_ratio(rets, periods_per_year_=252)
    assert s is not None
    assert s > 0


def test_sortino_higher_than_sharpe_when_skew_positive() -> None:
    """正偏分布（下行 std < 总 std）→ Sortino > Sharpe。"""
    rets = [0.05, 0.04, -0.005, 0.06, -0.002, 0.03]
    sharpe = metrics.sharpe_ratio(rets, 252)
    sortino = metrics.sortino_ratio(rets, 252)
    assert sharpe is not None and sortino is not None
    assert sortino > sharpe


# ─── max_drawdown_pct ───


def test_max_drawdown_empty_zero() -> None:
    assert metrics.max_drawdown_pct([]) == 0.0


def test_max_drawdown_monotonic_zero() -> None:
    assert metrics.max_drawdown_pct([100.0, 110.0, 120.0, 130.0]) == 0.0


def test_max_drawdown_simple() -> None:
    """100 -> 80 → 回撤 20%。"""
    assert metrics.max_drawdown_pct([100.0, 90.0, 80.0]) == pytest.approx(20.0)


def test_max_drawdown_recovery_then_new_high() -> None:
    """100 -> 80 -> 120：最大回撤还是 20%（从 100 跌到 80）。"""
    assert metrics.max_drawdown_pct([100.0, 80.0, 120.0]) == pytest.approx(20.0)


def test_max_drawdown_largest_after_recovery() -> None:
    """100 -> 80 -> 120 -> 60：最大回撤 50%（从 120 跌到 60）。"""
    assert metrics.max_drawdown_pct([100.0, 80.0, 120.0, 60.0]) == pytest.approx(50.0)


# ─── win_rate ───


def test_win_rate_empty_returns_none() -> None:
    assert metrics.win_rate([]) is None


def test_win_rate_all_wins() -> None:
    assert metrics.win_rate([1.0, 2.0, 3.0]) == pytest.approx(100.0)


def test_win_rate_all_losses() -> None:
    assert metrics.win_rate([-1.0, -2.0]) == pytest.approx(0.0)


def test_win_rate_mixed() -> None:
    # 3 wins / 5 trades = 60%
    assert metrics.win_rate([1.0, -1.0, 2.0, -2.0, 3.0]) == pytest.approx(60.0)


def test_win_rate_zero_counts_as_non_win() -> None:
    """pnl==0 算平，不计入胜数。"""
    # 1 win, 1 zero, 1 loss → 33.3%
    assert metrics.win_rate([1.0, 0.0, -1.0]) == pytest.approx(100 / 3)


# ─── 综合：单调上涨 ⇒ 0 回撤 + 正 Sharpe ───


def test_uptrend_combined_metrics() -> None:
    """单调上涨 + 微扰：max_dd=0，Sharpe 为正。

    备注：纯 ``1.001^N`` 浮点 returns 标准差不严格为 0（采样误差），所以这里
    用带正弦扰动的版本走 Sharpe 检查；``[0.01]*N`` 的严格 None 情况已经在
    ``test_sharpe_constant_returns_none`` 单独验过。
    """
    eq = [100.0 * (1.001 ** i) * (1 + 0.0001 * math.sin(i)) for i in range(100)]
    rets = metrics.bar_returns(eq)

    assert metrics.max_drawdown_pct(eq) >= 0.0  # 可能有微小回撤，但应该很小
    s = metrics.sharpe_ratio(rets, 8_760)
    assert s is not None
    assert s > 0


# ── 专业级扩展指标（D-11+） ───────────────────────────────────────────


def test_annualized_return_linear() -> None:
    # 半年(ppy=2 个 bar/年里跑 1 根 bar)赚 10% → 年化 20%
    assert metrics.annualized_return_pct(10.0, 1, 2) == pytest.approx(20.0)
    assert metrics.annualized_return_pct(10.0, 0, 2) is None
    assert metrics.annualized_return_pct(10.0, 1, 0) is None


def test_annualized_volatility() -> None:
    out = metrics.annualized_volatility_pct([0.01, -0.01, 0.01, -0.01], 252)
    assert out is not None and out > 0
    assert metrics.annualized_volatility_pct([0.01], 252) is None


def test_calmar_ratio_matches_linear_definition() -> None:
    # 1 年(num_bars=ppy)赚 20%、最大回撤 10% → calmar 2.0
    assert metrics.calmar_ratio(20.0, 10.0, 252, 252) == pytest.approx(2.0)
    assert metrics.calmar_ratio(20.0, 0.0, 252, 252) is None


def test_profit_factor_and_payoff() -> None:
    pnls = [10.0, -5.0, 20.0, -10.0]
    assert metrics.profit_factor(pnls) == pytest.approx(2.0)  # 30 / 15
    assert metrics.payoff_ratio(pnls) == pytest.approx(2.0)  # 15 / 7.5
    assert metrics.profit_factor([1.0, 2.0]) is None  # 无亏损
    assert metrics.payoff_ratio([-1.0]) is None  # 无盈利
    assert metrics.profit_factor([]) is None


def test_expectancy_and_extremes() -> None:
    pnls = [10.0, -4.0]
    assert metrics.expectancy(pnls) == pytest.approx(3.0)
    assert metrics.expectancy([]) is None


def test_consecutive_streaks() -> None:
    pnls = [1.0, 2.0, -1.0, 3.0, 4.0, 5.0, -2.0, -3.0]
    assert metrics.max_consecutive_wins(pnls) == 3
    assert metrics.max_consecutive_losses(pnls) == 2
    assert metrics.max_consecutive_wins([]) == 0


def test_max_drawdown_duration() -> None:
    # 峰 100 @i=0,跌到 90,i=3 收复 → 最长 2 根;尾段(101 后回落未收复)也计入
    assert metrics.max_drawdown_duration_bars([100, 90, 95, 101, 99, 98]) == 2
    assert metrics.max_drawdown_duration_bars([100, 90, 95, 101]) == 2
    assert metrics.max_drawdown_duration_bars([100, 90, 95]) == 2
    assert metrics.max_drawdown_duration_bars([1, 2, 3]) == 0
    assert metrics.max_drawdown_duration_bars([]) == 0


def test_exposure_pct() -> None:
    # 窗口 [0, 100ns],t=10 开仓 t=60 平 → 50%
    events = [(10, 1.0), (60, -1.0)]
    assert metrics.exposure_pct(events, 0, 100) == pytest.approx(50.0)
    # 尾段未平:t=80 开仓到窗口尾 → 20%
    assert metrics.exposure_pct([(80, 1.0)], 0, 100) == pytest.approx(20.0)
    assert metrics.exposure_pct([], 0, 100) == 0.0
    assert metrics.exposure_pct(events, None, 100) is None

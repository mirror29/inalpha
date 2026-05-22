"""回测绩效指标 —— 纯函数，无 IO，便于单测。

输入是已经准备好的 equity 序列 / 收益序列 / 单笔盈亏列表，
不和 ``Portfolio`` / ``BacktestEngine`` 耦合。这样后续给 ``services/evolver``
fitness 函数 / ``services/factor`` IC 评估等地方复用都很顺。

实现选择：

- 不依赖 numpy / pandas（MVP 用标准库 statistics + math 就够）
- ``timeframe`` → 年化系数对照表写死，超出 enum 抛错（保持纪律，timeframe
  必须在 ``schemas.TimeframeSchema`` 允许集合里）
- 无风险利率默认 0（MVP 不区分；E2 起再传参）
"""
from __future__ import annotations

import math
import statistics
from typing import Final

#: timeframe -> 每年 bar 数（用于年化）
#:
#: 1m=525600  5m=105120  15m=35040  1h=8760  4h=2190  1d=365
PERIODS_PER_YEAR: Final[dict[str, int]] = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
}


def periods_per_year(timeframe: str) -> int:
    """timeframe 字符串 -> 年化系数。未知 timeframe 抛 ``ValueError``。"""
    if timeframe not in PERIODS_PER_YEAR:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; supported: {sorted(PERIODS_PER_YEAR)}"
        )
    return PERIODS_PER_YEAR[timeframe]


def bar_returns(equity_curve: list[float]) -> list[float]:
    """从 equity 序列算每根 bar 的简单收益率（``e[i] / e[i-1] - 1``）。

    - 序列长度 < 2 返空列表
    - 上一根 equity ≤ 0 时跳过该点（避免除零 / 反号；正常账户碰不到）
    """
    rets: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        cur = equity_curve[i]
        if prev <= 0:
            continue
        rets.append(cur / prev - 1.0)
    return rets


def sharpe_ratio(
    returns: list[float],
    periods_per_year_: int,
    risk_free_rate: float = 0.0,
) -> float | None:
    """年化 Sharpe = ``mean(excess) / std(excess) * sqrt(periods_per_year)``。

    - 样本数 < 2 返 ``None``（std 无定义）
    - std == 0（完全平稳）返 ``None``（夏普不可定义）
    - 风险利率按 ``periods_per_year`` 等比拆成 per-period（默认 0 时无影响）
    """
    if len(returns) < 2:
        return None

    per_period_rf = risk_free_rate / periods_per_year_ if periods_per_year_ > 0 else 0.0
    excess = [r - per_period_rf for r in returns]

    mean = statistics.fmean(excess)
    stdev = statistics.stdev(excess)
    if stdev == 0:
        return None

    return mean / stdev * math.sqrt(periods_per_year_)


def sortino_ratio(
    returns: list[float],
    periods_per_year_: int,
    risk_free_rate: float = 0.0,
    target: float = 0.0,
) -> float | None:
    """年化 Sortino = ``mean(excess) / downside_dev * sqrt(periods_per_year)``。

    下行偏差 = sqrt(mean(min(r - target, 0)^2))。

    - 样本数 < 2 返 ``None``
    - 无下行（全为正收益）返 ``None``（无定义，比 ``+inf`` 安全）
    """
    if len(returns) < 2:
        return None

    per_period_rf = risk_free_rate / periods_per_year_ if periods_per_year_ > 0 else 0.0
    excess = [r - per_period_rf for r in returns]
    mean = statistics.fmean(excess)

    downside_sq = [min(r - target, 0.0) ** 2 for r in returns]
    downside_dev = math.sqrt(statistics.fmean(downside_sq))
    if downside_dev == 0:
        return None

    return mean / downside_dev * math.sqrt(periods_per_year_)


def max_drawdown_pct(equity_curve: list[float]) -> float:
    """最大回撤百分比（正数）。

    ``max((peak - trough) / peak * 100)``，要求 ``peak > 0``。

    - 空序列返 ``0.0``
    - 单调递增返 ``0.0``
    """
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def win_rate(trade_pnls: list[float]) -> float | None:
    """胜率（百分比）= 盈利笔数 / 总笔数 * 100。

    - 输入为 0 时返 ``None``（没交易无法定义胜率）
    - 严格 ``pnl > 0`` 算赢，``pnl == 0`` 算平（不计入分子）
    """
    if not trade_pnls:
        return None
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls) * 100.0

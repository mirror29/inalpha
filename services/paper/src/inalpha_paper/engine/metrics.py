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

#: timeframe -> 每年 bar 数（crypto 24/7，按 365 天算）
#:
#: 覆盖 CCXT / Binance 常见 timeframe；data-service connectors 支持哪些
#: 这里必须都列出，否则 backtest 直接 500（D-8b' review 高风险 #4）。
PERIODS_PER_YEAR: Final[dict[str, int]] = {
    # 分钟级
    "1m": 60 * 24 * 365,
    "3m": 20 * 24 * 365,
    "5m": 12 * 24 * 365,
    "15m": 4 * 24 * 365,
    "30m": 2 * 24 * 365,
    # 小时级
    "1h": 24 * 365,
    "2h": 12 * 365,
    "4h": 6 * 365,
    "6h": 4 * 365,
    "8h": 3 * 365,
    "12h": 2 * 365,
    # 日级及以上
    "1d": 365,
    "3d": 365 // 3,
    "1w": 52,
    "1M": 12,
}


def periods_per_year(timeframe: str) -> int:
    """timeframe 字符串 -> 年化系数。未知 timeframe 抛 ``ValueError``。

    支持 CCXT / Binance 全部常见 timeframe（crypto 24/7 假设）。如果上游加新
    timeframe，必须在这里同步登记，否则 metrics 算不出会把 backtest 整链路炸 500。
    """
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
    """最大回撤百分比（正数，**cap 在 100.0**）。

    ``max((peak - trough) / peak * 100)``，要求 ``peak > 0``。

    - 空序列返 ``0.0``
    - 单调递增返 ``0.0``
    - 物理上回撤最多 100%（账户全亏归零）；equity 跌穿零是"穿仓"信号，
      此时数学公式会算出 > 100% 的怪值（如 116.79% 来自 cash 透支 +
      持仓估值倒挂），无意义且误导。
      **本函数 cap 在 100.0**；穿仓信号由 ``detect_blew_up`` 单独提供。
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
    return min(max_dd, 100.0)


def detect_blew_up(equity_curve: list[float], initial_cash: float) -> bool:
    """检测账户是否"穿仓"——任意时点 equity ≤ 0 或 < -1% 初始本金的边界。

    物理意义上 spot 账户 equity 不应跌穿零（cash + 持仓估值最低 0）。出现 ≤ 0
    意味着撮合层未拦透支 / SHORT 爆仓 / 持仓估值因数据异常倒挂——这些场景下
    回测的 Sharpe / 收益率都是无意义数字，agent 应据此把整次回测标红或忽略。

    阈值不是严格 ``<= 0``：允许微小浮点误差（-1% × initial_cash），避免因
    fee 累计 cash 极小负数误报。
    """
    if not equity_curve or initial_cash <= 0:
        return False
    threshold = -0.01 * initial_cash
    return any(v <= threshold for v in equity_curve)


def win_rate(trade_pnls: list[float]) -> float | None:
    """胜率（百分比）= 盈利笔数 / 总笔数 * 100。

    - 输入为 0 时返 ``None``（没交易无法定义胜率）
    - 严格 ``pnl > 0`` 算赢，``pnl == 0`` 算平（不计入分子）
    """
    if not trade_pnls:
        return None
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls) * 100.0

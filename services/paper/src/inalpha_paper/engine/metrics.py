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


# ── 专业级扩展指标（D-11+ · 详情页「回测指标」扩容） ──────────────────
# 全部纯函数无 IO；None 语义同上：样本不足 / 数学未定义时返 None,调用方自行 fallback。


def annualized_return_pct(
    total_return_pct: float, num_bars: int, periods_per_year_: int
) -> float | None:
    """年化收益率（百分比，**线性换算**）。

    与 ``strategy_authoring.fitness.calmar_from_report`` 同口径（total / years,
    ADR-0020 未指定复利,全链路统一用线性近似,避免同页两个年化口径打架）。
    bar 数不足一根或年化系数非法返 ``None``。
    """
    if num_bars <= 0 or periods_per_year_ <= 0:
        return None
    years = num_bars / periods_per_year_
    if years <= 0:
        return None
    return total_return_pct / years


def annualized_volatility_pct(
    returns: list[float], periods_per_year_: int
) -> float | None:
    """年化波动率（百分比）= 每 bar 收益率样本标准差 × sqrt(年化系数) × 100。

    样本 < 2 或年化系数非法返 ``None``。
    """
    if len(returns) < 2 or periods_per_year_ <= 0:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return (var**0.5) * (periods_per_year_**0.5) * 100.0


def calmar_ratio(
    total_return_pct: float,
    max_drawdown_pct_: float,
    num_bars: int,
    periods_per_year_: int,
) -> float | None:
    """Calmar = 年化收益 / 最大回撤。回撤为 0 或样本不足返 ``None``。

    与 ``strategy_authoring.fitness.calmar_from_report`` 完全同式（该函数注明
    BacktestReport 落字段后可下线）。
    """
    ann = annualized_return_pct(total_return_pct, num_bars, periods_per_year_)
    if ann is None or max_drawdown_pct_ <= 0:
        return None
    return ann / max_drawdown_pct_


def profit_factor(trade_pnls: list[float]) -> float | None:
    """盈亏因子 = 毛利 / |毛损|。无亏损笔（毛损为 0）或无交易返 ``None``。"""
    if not trade_pnls:
        return None
    gross_profit = sum(p for p in trade_pnls if p > 0)
    gross_loss = -sum(p for p in trade_pnls if p < 0)
    if gross_loss <= 0:
        return None
    return gross_profit / gross_loss


def payoff_ratio(trade_pnls: list[float]) -> float | None:
    """平均盈亏比 = 平均盈利笔 / |平均亏损笔|。盈利或亏损任一侧为空返 ``None``。"""
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    if not wins or not losses:
        return None
    avg_win = sum(wins) / len(wins)
    avg_loss = -sum(losses) / len(losses)
    if avg_loss <= 0:
        return None
    return avg_win / avg_loss


def expectancy(trade_pnls: list[float]) -> float | None:
    """单笔期望（货币单位）= 全部 round-trip 盈亏均值。无交易返 ``None``。"""
    if not trade_pnls:
        return None
    return sum(trade_pnls) / len(trade_pnls)


def max_consecutive_wins(trade_pnls: list[float]) -> int:
    """最大连续盈利笔数（``pnl > 0`` 算赢,0 既不续也不断,按"非赢"处理断streak）。"""
    best = cur = 0
    for p in trade_pnls:
        cur = cur + 1 if p > 0 else 0
        best = max(best, cur)
    return best


def max_consecutive_losses(trade_pnls: list[float]) -> int:
    """最大连续亏损笔数（``pnl < 0`` 算亏）。"""
    best = cur = 0
    for p in trade_pnls:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def max_drawdown_duration_bars(equity_curve: list[float]) -> int:
    """最长回撤持续期（bar 数）——从 equity 创新高到**收复**该高点的最长间隔。

    结尾仍未收复的回撤也计入（截到序列末尾）。空 / 单调不降序列返 0。
    """
    if not equity_curve:
        return 0
    peak = equity_curve[0]
    peak_i = 0
    longest = 0
    for i, v in enumerate(equity_curve):
        if v >= peak:
            # 收复 bar 本身也计入间隔(否则比标准定义少 1 bar);
            # 仅当前一根仍低于 peak 才算"曾在回撤中",平走/连创新高不计。
            if i > peak_i and equity_curve[i - 1] < peak:
                longest = max(longest, i - peak_i)
            peak = v
            peak_i = i
        else:
            longest = max(longest, i - peak_i)
    return longest


def exposure_pct(
    fill_events: list[tuple[int, float]],
    period_start_ns: int | None,
    period_end_ns: int | None,
) -> float | None:
    """持仓时间占比（百分比）= 净仓位 ≠ 0 的时间 / 回测总时长。

    Args:
        fill_events: ``[(ts_ns, signed_qty_delta)]``,按时间升序（BUY 为正 SELL 为负）
        period_start_ns / period_end_ns: 回测窗口（纳秒）;缺任一返 ``None``

    无成交返 ``0.0``（全程空仓）。浮点累计的 |net| < 1e-12 视为已平。
    """
    if period_start_ns is None or period_end_ns is None:
        return None
    total = period_end_ns - period_start_ns
    if total <= 0:
        return None
    if not fill_events:
        return 0.0
    net = 0.0
    prev_ts = period_start_ns
    exposed = 0
    for ts, delta in fill_events:
        if abs(net) > 1e-12:
            exposed += ts - prev_ts
        prev_ts = ts
        net += delta
    if abs(net) > 1e-12:
        exposed += period_end_ns - prev_ts
    return min(max(exposed / total * 100.0, 0.0), 100.0)

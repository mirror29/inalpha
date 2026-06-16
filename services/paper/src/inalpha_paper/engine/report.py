"""``BacktestReport`` —— backtest 结束时的统计快照 + 绩效指标。

字段（D-7+）：

- 基础：``initial_cash`` / ``final_equity`` / ``total_return_pct`` / ``num_trades`` /
  ``total_fees`` / ``num_bars_processed`` / ``period_start`` / ``period_end``
- 绩效（新）：``sharpe`` / ``sortino`` / ``max_drawdown_pct`` / ``win_rate`` /
  ``equity_curve``（``[(ts_ns, equity), ...]``）
- 仓位：``positions`` —— 结束时还持有的仓位（用于 close-out 分析）

绩效指标用 ``engine.metrics`` 纯函数算，无 IO；测试用例直接给序列就能验。

策略：
- ``sharpe`` / ``sortino`` 当序列样本不足或 std=0 时返 ``None``（语义比 0 安全，调用方
  自己决定 fallback）
- ``win_rate`` 当没 round-trip trade 时返 ``None``
- ``max_drawdown_pct`` 始终返 float（无回撤为 0.0）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from ..kernel.clock import datetime_to_ns
from ..kernel.identifiers import InstrumentId
from ..model.orders import OrderSide
from ..model.positions import Position
from . import metrics
from .position_guard import PROTECTIVE_EXIT_TAGS
from .robustness import bootstrap_sharpe_ci

if TYPE_CHECKING:
    from .portfolio import Portfolio


@dataclass(frozen=True, slots=True)
class FillRecord:
    """回测期间单笔成交的快照 —— 逐笔成交复盘用（D-11+ · 详情页「回测成交」表）。

    由 ``Portfolio._handle_fill`` 每笔 fill 追加一条；``BacktestReport`` 带回主进程后
    落 ``backtest_trades`` 表。**纯原生字段，frozen+slots → 可 pickle**（子进程回传）。

    - ``realized_pnl``：本笔 fill 引起的 ``Position.realized_pnl`` 增量（开仓笔=0，
      平仓/反手笔=该笔价差盈亏，**不含手续费**，与 ``Portfolio.closed_trade_pnls`` 同口径）
    - ``bar_close``：成交当时的 mark（撮合早于本根 bar mark 更新，缺失时退回 ``fill_price``，近似）
    - ``intent``：按成交前持仓方向 + side 派生（open_long / open_short / close）
    """
    ts_ns: int
    bar_close: float
    side: str
    quantity: float
    order_type: str
    fill_price: float
    fee: float
    realized_pnl: float
    intent: str | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestReport:
    initial_cash: float
    final_equity: float
    total_return_pct: float

    num_trades: int
    total_fees: float

    num_bars_processed: int
    period_start: datetime | None
    period_end: datetime | None

    positions: dict[InstrumentId, Position]

    #: 年化 Sharpe；样本不足或波动率为 0 时为 ``None``
    sharpe: float | None = None
    #: 年化 Sortino；样本不足或无下行时为 ``None``
    sortino: float | None = None
    #: 最大回撤百分比（正数，无回撤为 0.0，**cap 100.0**）
    max_drawdown_pct: float = 0.0
    #: 胜率百分比；没 round-trip 时为 ``None``
    win_rate: float | None = None
    #: ``[(ts_ns, equity)]`` 序列
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    #: ── 专业级扩展指标（D-11+,均可 None = 样本不足/未定义） ──
    #: 年化收益（线性换算,与 fitness.calmar_from_report 同口径）
    annualized_return_pct: float | None = None
    #: 年化波动率
    annualized_volatility_pct: float | None = None
    #: Calmar = 年化收益 / 最大回撤
    calmar: float | None = None
    #: 盈亏因子 = 毛利 / |毛损|
    profit_factor: float | None = None
    #: 平均盈亏比 = 平均盈利笔 / |平均亏损笔|
    payoff_ratio: float | None = None
    #: 单笔期望（货币）
    expectancy: float | None = None
    #: 最佳 / 最差单笔（货币,round-trip 口径）
    best_trade_pnl: float | None = None
    worst_trade_pnl: float | None = None
    #: 最大连胜 / 连亏笔数。恒为 int（无成交 = 0,与"零连胜"同值——要区分
    #: 看 num_trades==0）,不用 Optional 假装会出 None。
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    #: 最长回撤持续期（bar 数,含未收复的尾段;恒为 int,无回撤 = 0）
    max_drawdown_duration_bars: int = 0
    #: 持仓时间占比（%）
    exposure_pct: float | None = None
    #: 逐笔成交（含每笔实现盈亏），落 ``backtest_trades`` 表用
    fills: list[FillRecord] = field(default_factory=list)
    #: ADR-0052：框架级持仓保护止损触发的出场笔数（tag ∈ stop_loss/take_profit/
    #: trailing_stop_loss）。> 0 表示回测期间灾难兜底生效过几次——agent / 前端可据此
    #: 看到"如果未来这样跑，框架会在哪些点止血"，把回测对未来的兜底可见化。
    protective_exits: int = 0
    #: 账户是否"穿仓"——任意时点 equity 跌破 -1% × initial_cash（物理上 spot
    #: 账户 equity 不应 < 0）。True 表示本次回测结果在物理上不可信，agent /
    #: 前端应当显式警告，不要直接渲染 Sharpe / 收益率（数学正确但语义无效）。
    blew_up: bool = False
    #: 物理一致性警告列表，例如 "账户穿仓"、"现金最终为负"。空列表 = 干净。
    #: 前端 / orchestrator agent 见非空时必须告警，禁止无声渲染。
    health_warnings: list[str] = field(default_factory=list)
    #: ── 防过拟合：Bootstrap Sharpe 95% CI（ADR-0027 单次回测路径，年化口径，与
    #: ``sharpe`` 同尺度）。样本不足 / Sharpe 未定义 / 穿仓时为 None。
    #: ``sharpe_ci_includes_zero=True`` ⇒ "回测 Sharpe 看起来好，但统计上不显著为正"——
    #: 这是把"看起来好"和"真的好"分开的第一道闸（详 ADR-0026 反过拟合体系）。
    sharpe_ci_lower: float | None = None
    sharpe_ci_upper: float | None = None
    sharpe_ci_includes_zero: bool | None = None

    @classmethod
    def from_portfolio(
        cls,
        portfolio: Portfolio,
        num_bars: int,
        period_start: datetime | None,
        period_end: datetime | None,
        timeframe: str,
    ) -> BacktestReport:
        """从 ``Portfolio`` 状态 + 元数据构造完整报告。

        实现注：把"找 timeframe 对应年化系数 / 算 sharpe / sortino / dd / win_rate"
        集中在这里，避免 ``BacktestEngine`` 知道指标细节。
        """
        equity_curve = portfolio.equity_curve
        equity_values = [eq for _ts, eq in equity_curve]
        returns = metrics.bar_returns(equity_values)
        ppy = metrics.periods_per_year(timeframe)
        final_equity_v = portfolio.equity()
        final_cash_v = portfolio.cash

        blew_up = metrics.detect_blew_up(equity_values, portfolio.initial_cash)
        warnings: list[str] = []
        if blew_up:
            warnings.append(
                "账户穿仓：回测期间 equity 跌破 -1% × initial_cash，物理上不应发生；"
                "通常意味着撮合层未拦透支或 SHORT 爆仓，本次 Sharpe / 收益率不可信"
            )
        if final_cash_v < -0.01 * portfolio.initial_cash:
            warnings.append(
                f"现金最终为负 ({final_cash_v:.2f})：撮合层透支拦截缺失"
            )
        if final_equity_v < 0:
            warnings.append(
                f"最终 equity 为负 ({final_equity_v:.2f})：账户实际已破产"
            )

        total_return = portfolio.total_return_pct()
        max_dd = metrics.max_drawdown_pct(equity_values)
        trade_pnls = portfolio.closed_trade_pnls
        fills = list(portfolio.fills)
        protective_exits = sum(1 for f in fills if f.tag in PROTECTIVE_EXIT_TAGS)

        sharpe_value = metrics.sharpe_ratio(returns, ppy)
        # ADR-0027 防过拟合：单次回测给 Bootstrap Sharpe 95% CI。仅在 Sharpe 有定义
        # （样本足 + 有波动）且账户未穿仓时算——穿仓的 CI 物理上无意义。bootstrap 返
        # per-bar Sharpe，按 √ppy 年化到与 sharpe_value 同口径；includes_zero 是符号
        # 判据，年化不改变其真值。
        ci_lower: float | None = None
        ci_upper: float | None = None
        ci_includes_zero: bool | None = None
        if sharpe_value is not None and not blew_up and len(returns) >= 2:
            try:
                # n_samples=2000（默认 10000 的 1/5）是性能权衡：每次回测都同步算 CI，
                # 2000 次重采样把延迟压到 ~ms 级。代价是 Monte Carlo 误差约 √5≈2.2×；
                # Sharpe 极接近 0 时 includes_zero 可能在调用间抖动——但那本就是"统计上
                # 不显著"的灰区，判定为不可信方向一致，不影响结论。长序列要更稳可调高。
                _ci = bootstrap_sharpe_ci(returns, n_samples=2000)
                _ann = math.sqrt(ppy)
                ci_lower = _ci.ci_lower * _ann
                ci_upper = _ci.ci_upper * _ann
                ci_includes_zero = _ci.ci_includes_zero
            except Exception:  # CI 是非关键增强，任何失败都不能拖垮主回测
                # bootstrap 内部 numpy 对边缘分布（退化 returns / 极偏态 / 浮点噪声）
                # 可能抛 ValueError 以外的 LinAlgError / RuntimeError / FloatingPointError。
                # 防过拟合 CI 算不出 → 静默维持 None，绝不让 from_portfolio 整体抛 → 500（CR Major）。
                pass
        # exposure 用回测窗口端点（datetime → ns,整数路径:float 对 2026 时间戳
        # 丢 ~100ns,会与 fill.ts_ns 的整数路径错位）;缺端点时为 None。
        start_ns = datetime_to_ns(period_start) if period_start else None
        end_ns = datetime_to_ns(period_end) if period_end else None
        fill_events = [
            # 用枚举值常量而非字面量:OrderSide 值若改写法,这里跟着编译期变,
            # 不会静默把所有 BUY 当 SELL 算 exposure。
            (f.ts_ns, f.quantity if f.side == OrderSide.BUY.value else -f.quantity)
            for f in fills
        ]

        return cls(
            initial_cash=portfolio.initial_cash,
            final_equity=final_equity_v,
            total_return_pct=total_return,
            num_trades=portfolio.trade_count,
            total_fees=portfolio.total_fees,
            num_bars_processed=num_bars,
            period_start=period_start,
            period_end=period_end,
            positions=portfolio.positions(),
            sharpe=sharpe_value,
            sortino=metrics.sortino_ratio(returns, ppy),
            max_drawdown_pct=max_dd,
            win_rate=metrics.win_rate(trade_pnls),
            equity_curve=equity_curve,
            fills=fills,
            annualized_return_pct=metrics.annualized_return_pct(
                total_return, num_bars, ppy
            ),
            annualized_volatility_pct=metrics.annualized_volatility_pct(returns, ppy),
            calmar=metrics.calmar_ratio(total_return, max_dd, num_bars, ppy),
            profit_factor=metrics.profit_factor(trade_pnls),
            payoff_ratio=metrics.payoff_ratio(trade_pnls),
            expectancy=metrics.expectancy(trade_pnls),
            best_trade_pnl=max(trade_pnls) if trade_pnls else None,
            worst_trade_pnl=min(trade_pnls) if trade_pnls else None,
            max_consecutive_wins=metrics.max_consecutive_wins(trade_pnls),
            max_consecutive_losses=metrics.max_consecutive_losses(trade_pnls),
            max_drawdown_duration_bars=metrics.max_drawdown_duration_bars(
                equity_values
            ),
            exposure_pct=metrics.exposure_pct(fill_events, start_ns, end_ns),
            protective_exits=protective_exits,
            blew_up=blew_up,
            health_warnings=warnings,
            sharpe_ci_lower=ci_lower,
            sharpe_ci_upper=ci_upper,
            sharpe_ci_includes_zero=ci_includes_zero,
        )

    def __str__(self) -> str:
        sign = "+" if self.total_return_pct >= 0 else ""
        sharpe = f"{self.sharpe:.2f}" if self.sharpe is not None else "n/a"
        win = f"{self.win_rate:.1f}%" if self.win_rate is not None else "n/a"
        return (
            f"BacktestReport(equity={self.final_equity:.2f} "
            f"return={sign}{self.total_return_pct:.2f}% "
            f"sharpe={sharpe} maxDD={self.max_drawdown_pct:.2f}% "
            f"win={win} trades={self.num_trades} "
            f"fees={self.total_fees:.2f} bars={self.num_bars_processed})"
        )

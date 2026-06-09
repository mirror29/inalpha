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

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from ..kernel.identifiers import InstrumentId
from ..model.positions import Position
from . import metrics

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
    #: 逐笔成交（含每笔实现盈亏），落 ``backtest_trades`` 表用
    fills: list[FillRecord] = field(default_factory=list)
    #: 账户是否"穿仓"——任意时点 equity 跌破 -1% × initial_cash（物理上 spot
    #: 账户 equity 不应 < 0）。True 表示本次回测结果在物理上不可信，agent /
    #: 前端应当显式警告，不要直接渲染 Sharpe / 收益率（数学正确但语义无效）。
    blew_up: bool = False
    #: 物理一致性警告列表，例如 "账户穿仓"、"现金最终为负"。空列表 = 干净。
    #: 前端 / orchestrator agent 见非空时必须告警，禁止无声渲染。
    health_warnings: list[str] = field(default_factory=list)

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

        return cls(
            initial_cash=portfolio.initial_cash,
            final_equity=final_equity_v,
            total_return_pct=portfolio.total_return_pct(),
            num_trades=portfolio.trade_count,
            total_fees=portfolio.total_fees,
            num_bars_processed=num_bars,
            period_start=period_start,
            period_end=period_end,
            positions=portfolio.positions(),
            sharpe=metrics.sharpe_ratio(returns, ppy),
            sortino=metrics.sortino_ratio(returns, ppy),
            max_drawdown_pct=metrics.max_drawdown_pct(equity_values),
            win_rate=metrics.win_rate(portfolio.closed_trade_pnls),
            equity_curve=equity_curve,
            fills=list(portfolio.fills),
            blew_up=blew_up,
            health_warnings=warnings,
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

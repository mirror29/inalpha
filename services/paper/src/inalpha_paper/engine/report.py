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
    #: 最大回撤百分比（正数，无回撤为 0.0）
    max_drawdown_pct: float = 0.0
    #: 胜率百分比；没 round-trip 时为 ``None``
    win_rate: float | None = None
    #: ``[(ts_ns, equity)]`` 序列
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

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

        return cls(
            initial_cash=portfolio.initial_cash,
            final_equity=portfolio.equity(),
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

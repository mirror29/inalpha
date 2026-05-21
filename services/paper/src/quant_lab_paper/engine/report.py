"""``BacktestReport`` —— backtest 结束时的统计快照。

MVP 字段（D-5）：

- ``initial_cash`` / ``final_equity`` / ``total_return_pct``
- ``num_trades`` / ``total_fees``
- ``num_bars_processed`` / ``period_start`` / ``period_end``
- ``positions`` —— 结束时还持有的仓位（用于 close-out 分析）

后续（D-6+）补：sharpe / sortino / max_drawdown / win_rate / equity_curve。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..kernel.identifiers import InstrumentId
from ..model.positions import Position


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

    def __str__(self) -> str:
        sign = "+" if self.total_return_pct >= 0 else ""
        return (
            f"BacktestReport(equity={self.final_equity:.2f} "
            f"return={sign}{self.total_return_pct:.2f}% "
            f"trades={self.num_trades} fees={self.total_fees:.2f} "
            f"bars={self.num_bars_processed})"
        )

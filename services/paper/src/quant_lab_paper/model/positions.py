"""``Position`` —— 净持仓 + 平均价 + 已实现盈亏。

带 ``generation`` 字段（[ADR-0013 CAS](../../../../docs/decisions/0013-stale-state-detection.md)）：
每次 ``apply_fill`` / ``apply_close`` 都 +1，写操作必须传 ``expected_generation`` 校验。

`Position.from_fills(fills)` 让 ADR-0017 reconcile 能从事件流重建 —— live worker
crash 重启时第一步就是用 broker 真实成交 reconcile cache。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..kernel.identifiers import InstrumentId
from .orders import OrderSide


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"  # 无持仓


@dataclass(slots=True)
class Position:
    """单标的净持仓。

    ``quantity`` 带符号：LONG 为正，SHORT 为负，FLAT 为 0。
    """

    instrument_id: InstrumentId
    quantity: float = 0.0
    avg_open_price: float = 0.0
    realized_pnl: float = 0.0
    generation: int = 1
    """ADR-0013 CAS 字段：每次 mutation +1。"""

    ts_opened: int = 0
    ts_last_event: int = 0
    _fill_history: list[tuple[OrderSide, float, float, int]] = field(default_factory=list)
    """list of (side, quantity, price, ts) 供 reconcile 用。"""

    @property
    def side(self) -> PositionSide:
        if self.quantity > 1e-9:
            return PositionSide.LONG
        if self.quantity < -1e-9:
            return PositionSide.SHORT
        return PositionSide.FLAT

    @property
    def is_flat(self) -> bool:
        return self.side == PositionSide.FLAT

    def unrealized_pnl(self, mark_price: float) -> float:
        """按给定 mark price 算浮盈。``quantity`` 带符号已正确处理 LONG/SHORT。"""
        if self.is_flat:
            return 0.0
        return self.quantity * (mark_price - self.avg_open_price)

    def apply_fill(self, side: OrderSide, fill_quantity: float, fill_price: float, ts: int) -> None:
        """应用一次成交。

        - 同方向：加仓 → 加权平均更新 ``avg_open_price``
        - 反方向：减仓 / 平仓 / 反向开仓 → 结算已实现盈亏
        """
        if fill_quantity <= 0:
            raise ValueError(f"fill_quantity must be positive, got {fill_quantity}")

        delta = fill_quantity if side == OrderSide.BUY else -fill_quantity
        prev_qty = self.quantity
        new_qty = prev_qty + delta

        if prev_qty == 0:
            # 开仓
            self.avg_open_price = fill_price
            self.ts_opened = ts
        elif (prev_qty > 0) == (delta > 0):
            # 同方向加仓：加权平均
            total_cost = abs(prev_qty) * self.avg_open_price + abs(delta) * fill_price
            self.avg_open_price = total_cost / abs(new_qty) if new_qty != 0 else 0.0
        else:
            # 反方向：先算这次成交的已实现盈亏
            closed_qty = min(abs(prev_qty), abs(delta))
            if prev_qty > 0:
                # 原 LONG，卖出 → 收益 = (fill_price - avg) * closed_qty
                self.realized_pnl += (fill_price - self.avg_open_price) * closed_qty
            else:
                # 原 SHORT，买回 → 收益 = (avg - fill_price) * closed_qty
                self.realized_pnl += (self.avg_open_price - fill_price) * closed_qty

            if abs(new_qty) < 1e-9:
                # 完全平仓
                self.avg_open_price = 0.0
                new_qty = 0.0
            elif (new_qty > 0) != (prev_qty > 0):
                # 反向开仓：剩下的部分以 fill_price 当新成本
                self.avg_open_price = fill_price
                self.ts_opened = ts

        self.quantity = new_qty
        self.ts_last_event = ts
        self.generation += 1
        self._fill_history.append((side, fill_quantity, fill_price, ts))

    @classmethod
    def from_fills(
        cls,
        instrument_id: InstrumentId,
        fills: list[tuple[OrderSide, float, float, int]],
    ) -> Position:
        """从成交事件流重建 Position。

        ADR-0017 live worker reconcile 用——crash 重启后，从 broker 拿到真实成交序列
        重新放给空白 Position 跑一遍 ``apply_fill``，即可对齐状态。
        """
        pos = cls(instrument_id=instrument_id)
        for side, qty, price, ts in fills:
            pos.apply_fill(side, qty, price, ts)
        return pos

"""全局 ID 类型。

设计约定（参考 vnpy 的 vt_orderid / vt_symbol 路径，简化为 dataclass + NewType 混搭）：

- ``InstrumentId`` —— 标的，``BTC/USDT@binance`` 这种语义
- ``ClientOrderId`` / ``VenueOrderId`` —— 订单 ID 双向索引（ADR-0013 防 fill 去重）
- ``StrategyId`` —— 策略实例 ID（绑定 events.order.<strategy> topic）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

# ─── 简单字符串包装 ───
# 用 NewType 比 dataclass 轻量；不需要解析逻辑就用这个

ClientOrderId = NewType("ClientOrderId", str)
"""系统生成的订单 ID，submit 前确定。"""

VenueOrderId = NewType("VenueOrderId", str)
"""交易所分配的订单 ID，accept 回报里才有。"""

StrategyId = NewType("StrategyId", str)
"""策略实例 ID，绑定 ``events.order.<strategy>`` 等 topic。"""


# ─── 复合 ID（dataclass） ───


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """``symbol@venue`` 这种语义的标的 ID。

    Example::

        InstrumentId(symbol="BTC/USDT", venue="binance")  # → "BTC/USDT@binance"
    """

    symbol: str
    venue: str

    def __str__(self) -> str:
        return f"{self.symbol}@{self.venue}"

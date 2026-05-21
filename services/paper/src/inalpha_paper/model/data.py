"""行情数据模型 —— 不可变 dataclass，全部带双时间戳 + ``data_epoch``。

``data_epoch`` 是 [ADR-0013](../../../../docs/decisions/0013-stale-state-detection.md) 的落地：
每次数据连接重连 +1，策略 / 模型在跨 epoch 时必须 reset indicator。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..kernel.identifiers import InstrumentId


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """L1 quote tick —— 最优买卖价 + 量。

    Args:
        instrument_id: 标的
        bid_price / ask_price: 最优买卖价
        bid_size / ask_size: 最优买卖量
        ts_event: 事件发生时间（ns，venue 给的时间戳）
        ts_init: 系统接到的时间（ns）
        data_epoch: 数据连接 epoch，重连 +1。跨 epoch 数据不可信
        is_stale_after_reconnect: 重连后第一个 tick 标 True，提醒下游重新对齐
    """

    instrument_id: InstrumentId
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    ts_event: int
    ts_init: int
    data_epoch: int = 1
    is_stale_after_reconnect: bool = False


@dataclass(frozen=True, slots=True)
class TradeTick:
    """逐笔成交。"""

    instrument_id: InstrumentId
    price: float
    size: float
    aggressor_side: str  # 'BUY' / 'SELL' / 'NONE'
    trade_id: str
    ts_event: int
    ts_init: int
    data_epoch: int = 1
    is_stale_after_reconnect: bool = False


@dataclass(frozen=True, slots=True)
class Bar:
    """K 线。``timeframe`` 走 CCXT 风格字符串（``1m`` / ``5m`` / ``1h`` / ``1d``）。"""

    instrument_id: InstrumentId
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_event: int
    ts_init: int
    data_epoch: int = 1
    is_stale_after_reconnect: bool = False

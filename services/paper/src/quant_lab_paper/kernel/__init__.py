"""内核：时间源 / 消息总线 / ID 类型。

所有引擎组件（Strategy / Gateway / RiskEngine / ExecutionEngine / Portfolio）
共享同一个 Clock 与 MessageBus 实例。回测 vs 实盘的差异封装在 Clock 子类与
Gateway 子类里——**Strategy 代码 0 行改动**（参考 refs/nautilus.md §4）。
"""
from .clock import Clock, LiveClock, TestClock, TimeEvent
from .identifiers import ClientOrderId, InstrumentId, StrategyId, VenueOrderId
from .msgbus import MessageBus

__all__ = [
    "ClientOrderId",
    "Clock",
    "InstrumentId",
    "LiveClock",
    "MessageBus",
    "StrategyId",
    "TestClock",
    "TimeEvent",
    "VenueOrderId",
]

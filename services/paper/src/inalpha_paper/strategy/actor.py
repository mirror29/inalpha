"""``Actor`` —— 数据订阅 + 生命周期回调（不下单）。

用户子类化 ``Actor`` 实现纯研究 / 监控逻辑，覆盖 ``on_*`` 回调。
"""
from __future__ import annotations

from ..kernel.clock import Clock
from ..kernel.identifiers import InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar, QuoteTick, TradeTick


class Actor:
    """数据订阅 + 生命周期。"""

    def __init__(self, name: str, clock: Clock, msgbus: MessageBus) -> None:
        self._name = name
        self._clock = clock
        self._msgbus = msgbus

    # ─── 公共属性（子类可读） ───

    @property
    def name(self) -> str:
        return self._name

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def msgbus(self) -> MessageBus:
        return self._msgbus

    # ─── 数据订阅（通过 msgbus 自动路由到 on_*） ───

    def subscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        topic = f"data.quotes.{instrument_id.venue}.{instrument_id.symbol}"
        self._msgbus.subscribe(topic, self._handle_quote_tick)

    def subscribe_trade_ticks(self, instrument_id: InstrumentId) -> None:
        topic = f"data.trades.{instrument_id.venue}.{instrument_id.symbol}"
        self._msgbus.subscribe(topic, self._handle_trade_tick)

    def subscribe_bars(self, instrument_id: InstrumentId, timeframe: str) -> None:
        topic = f"data.bars.{instrument_id.venue}.{instrument_id.symbol}.{timeframe}"
        self._msgbus.subscribe(topic, self._handle_bar)

    # ─── 框架内部分发（用户不应直接调） ───

    def _handle_quote_tick(self, msg: object) -> None:
        if isinstance(msg, QuoteTick):
            self.on_quote_tick(msg)

    def _handle_trade_tick(self, msg: object) -> None:
        if isinstance(msg, TradeTick):
            self.on_trade_tick(msg)

    def _handle_bar(self, msg: object) -> None:
        if isinstance(msg, Bar):
            self.on_bar(msg)

    # ─── 用户覆盖的回调（默认 no-op） ───

    def on_start(self) -> None:
        """Actor 启动时调一次。"""

    def on_stop(self) -> None:
        """Actor 停止时调一次。"""

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """每个 quote tick 触发。"""

    def on_trade_tick(self, tick: TradeTick) -> None:
        """每个 trade tick 触发。"""

    def on_bar(self, bar: Bar) -> None:
        """每根 bar 触发。"""

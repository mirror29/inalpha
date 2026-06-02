"""``engine.live_session.LiveEngineSession`` 测试（D-11）——纯内存，无 DB。

验证：(1) 策略 submit_order 被拦截、本进程不撮合；(2) confirm_fill 回灌后 session
内 Portfolio 持仓视图更新（保证策略下一根 bar 看到自己的仓位）。
"""
from __future__ import annotations

from uuid import uuid4

from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.base import Strategy

_INSTRUMENT = InstrumentId(symbol="BTC/USDT", venue="binance")


class _BuyOnceStrategy(Strategy):
    """第一根 bar 市价买 1 单位，之后不动。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._bought = False
        self.filled_events: list = []

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if not self._bought:
            self._bought = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"t-{uuid4().hex[:8]}"),
                    instrument_id=self._instrument_id,
                    side=OrderSide.BUY,
                    type=OrderType.MARKET,
                    quantity=1.0,
                )
            )

    def on_order_filled(self, event) -> None:  # type: ignore[no-untyped-def]
        self.filled_events.append(event)


class _StopOrderStrategy(Strategy):
    """第一根 bar 提交一个 STOP_MARKET 单（live runner 不支持，应被守门拒）。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if not self._sent:
            self._sent = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"stop-{uuid4().hex[:8]}"),
                    instrument_id=self._instrument_id,
                    side=OrderSide.SELL,
                    type=OrderType.STOP_MARKET,
                    quantity=1.0,
                )
            )


def test_stop_order_type_rejected_not_collected() -> None:
    """STOP_MARKET 单被 _CaptureGateway 守门拒（不进 collected），避免下游 assert 崩。"""
    session = LiveEngineSession(
        strategy_cls=_StopOrderStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={}, initial_cash=10_000.0, fee_rate=0.001,
    )
    orders = session.feed_bar(_bar(1_000_000_000))
    # 不支持的类型被拒 → 不收集 → 不会流到 plan/exec 触发 AssertionError
    assert orders == []
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


def _bar(ts_ns: int, close: float = 100.0) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT, timeframe="1h",
        open=close, high=close, low=close, close=close, volume=1.0,
        ts_event=ts_ns, ts_init=ts_ns,
    )


def _make_session() -> LiveEngineSession:
    return LiveEngineSession(
        strategy_cls=_BuyOnceStrategy,
        instrument_id=_INSTRUMENT,
        timeframe="1h",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )


def test_feed_bar_collects_order_without_filling() -> None:
    session = _make_session()
    orders = session.feed_bar(_bar(1_000_000_000))
    # 收集到 1 笔下单意图
    assert len(orders) == 1
    order, _strategy_id = orders[0]
    assert order.side == OrderSide.BUY
    # 但本进程未撮合 → portfolio 仍空仓
    assert session.portfolio.position(_INSTRUMENT) is None or session.portfolio.position(
        _INSTRUMENT
    ).is_flat


def test_confirm_fill_updates_portfolio_and_strategy() -> None:
    session = _make_session()
    orders = session.feed_bar(_bar(1_000_000_000, close=100.0))
    order, strategy_id = orders[0]

    session.confirm_fill(
        order=order, strategy_id=strategy_id,
        fill_qty=1.0, fill_price=100.0, ts_event=1_000_000_000,
    )

    # portfolio 持仓视图更新（策略下一根 bar 能看到自己的仓位）
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is not None
    assert pos.quantity == 1.0
    # 策略也收到 OrderFilled 回调
    assert len(session._strategy.filled_events) == 1  # type: ignore[attr-defined]


def test_reject_order_keeps_portfolio_flat() -> None:
    session = _make_session()
    orders = session.feed_bar(_bar(1_000_000_000))
    order, strategy_id = orders[0]

    session.reject_order(
        order=order, strategy_id=strategy_id, reason="RISK_REJECTED: test", ts_event=1_000_000_000
    )

    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


def test_second_bar_after_fill_sees_position() -> None:
    """回灌后第二根 bar，策略 portfolio 视图保留持仓（不再以为空仓）。"""
    session = _make_session()
    orders = session.feed_bar(_bar(1_000_000_000))
    order, strategy_id = orders[0]
    session.confirm_fill(
        order=order, strategy_id=strategy_id, fill_qty=1.0, fill_price=100.0,
        ts_event=1_000_000_000,
    )
    # 第二根 bar：_BuyOnceStrategy 不再下单（已买过），不应有新 order
    orders2 = session.feed_bar(_bar(2_000_000_000))
    assert orders2 == []
    assert session.portfolio.position(_INSTRUMENT).quantity == 1.0  # type: ignore[union-attr]

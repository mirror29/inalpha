"""``RiskEngine`` + ``ExecutionEngine`` + ``Portfolio`` 三件套集成测试。

直接搭好整条 Strategy → Risk → Execution → Exchange → 回报 链路，验证：

- 提交订单走通整条链
- Order 状态机正确推进（NEW → SUBMITTED → ACCEPTED → FILLED）
- Strategy 收到正确的 OrderSubmitted / Accepted / Filled 事件
- Portfolio 更新持仓 + 现金 + 手续费
- 跨 strategy 事件隔离
"""
from __future__ import annotations

from typing import Any, cast

from quant_lab_paper.engine.portfolio import Portfolio
from quant_lab_paper.execution.exchange import SimulatedExchange
from quant_lab_paper.execution.execution_engine import ExecutionEngine
from quant_lab_paper.execution.risk_engine import RiskEngine
from quant_lab_paper.kernel.clock import TestClock
from quant_lab_paper.kernel.identifiers import ClientOrderId, InstrumentId
from quant_lab_paper.kernel.msgbus import MessageBus
from quant_lab_paper.model.data import Bar
from quant_lab_paper.model.events import (
    OrderAccepted,
    OrderFilled,
    OrderSubmitted,
    PositionOpened,
)
from quant_lab_paper.model.orders import Order, OrderSide, OrderStatus, OrderType
from quant_lab_paper.strategy.base import Strategy


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _make_bar(open: float = 100.0, high: float = 105.0, low: float = 95.0,
              close: float = 102.0, ts: int = 1000) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        ts_event=ts,
        ts_init=ts,
    )


class _CapturingStrategy(Strategy):
    """记录所有收到的事件以便 assert。"""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.submitted: list[OrderSubmitted] = []
        self.accepted: list[OrderAccepted] = []
        self.fills: list[OrderFilled] = []
        self.opened: list[PositionOpened] = []

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        self.submitted.append(event)

    def on_order_accepted(self, event: OrderAccepted) -> None:
        self.accepted.append(event)

    def on_order_filled(self, event: OrderFilled) -> None:
        self.fills.append(event)

    def on_position_opened(self, event: PositionOpened) -> None:
        self.opened.append(event)


def _build_stack(initial_cash: float = 10_000.0, fee_rate: float = 0.001) -> dict[str, Any]:
    """组装完整的执行链。"""
    clock = TestClock(0)
    bus = MessageBus()

    exchange = SimulatedExchange(bus, clock)
    execution = ExecutionEngine(bus, exchange)
    risk = RiskEngine(bus)
    portfolio = Portfolio(bus, initial_cash=initial_cash, fee_rate=fee_rate)

    return {
        "clock": clock,
        "bus": bus,
        "exchange": exchange,
        "execution": execution,
        "risk": risk,
        "portfolio": portfolio,
    }


# ─── 整链路：submit → accepted → fill ───


def test_submit_market_buy_full_chain() -> None:
    stack = _build_stack()
    bus = cast(MessageBus, stack["bus"])
    exchange = cast(SimulatedExchange, stack["exchange"])
    portfolio = cast(Portfolio, stack["portfolio"])
    execution = cast(ExecutionEngine, stack["execution"])

    strat = _CapturingStrategy("s1", stack["clock"], bus)
    stack["clock"].set_time(500)

    order = Order(
        client_order_id=ClientOrderId("c-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=0.1,
    )
    strat.submit_order(order)

    # submit → Strategy 收到 OrderSubmitted + OrderAccepted（venue 同步 accept）
    assert len(strat.submitted) == 1
    assert len(strat.accepted) == 1
    # 状态：ACCEPTED，pending 中
    assert exchange.pending_count() == 1
    assert order.status == OrderStatus.ACCEPTED

    # 推一根 bar 让 venue 撮合
    bar = _make_bar(open=100.0, ts=1000)
    exchange.process_bar(bar)

    # Strategy 收到 OrderFilled
    assert len(strat.fills) == 1
    fill = strat.fills[0]
    assert fill.fill_quantity == 0.1
    assert fill.fill_price == 100.0
    assert fill.is_last_fill is True
    # Order 走完状态机
    assert order.status == OrderStatus.FILLED
    # 进入终态后 execution 清理
    assert execution.active_count() == 0

    # Portfolio：现金扣了 0.1 * 100 + 手续费 = 10 + 0.01 = 10.01；持仓 = 0.1 BTC
    assert portfolio.cash == 10_000.0 - 10.0 - 0.01
    assert portfolio.total_fees == 0.01
    assert portfolio.trade_count == 1
    pos = portfolio.position(_btc())
    assert pos is not None
    assert pos.quantity == 0.1
    assert pos.avg_open_price == 100.0
    # PositionOpened 派发
    assert len(strat.opened) == 1


# ─── 现金 + equity ───


def test_equity_with_mark_to_market() -> None:
    stack = _build_stack(initial_cash=10_000.0, fee_rate=0.0)  # 零手续费
    bus = cast(MessageBus, stack["bus"])
    exchange = cast(SimulatedExchange, stack["exchange"])
    portfolio = cast(Portfolio, stack["portfolio"])

    strat = _CapturingStrategy("s1", stack["clock"], bus)

    order = Order(
        client_order_id=ClientOrderId("c-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=1.0,
    )
    strat.submit_order(order)

    # bar.open=100 撮合，1.0 BTC @ 100 = 100 USD
    bar1 = _make_bar(open=100.0, close=100.0, ts=1000)
    exchange.process_bar(bar1)
    portfolio.update_mark(_btc(), 100.0)

    # 现金 9900，持仓 1 BTC mark=100 → equity = 9900 + 100 = 10000
    assert portfolio.equity() == 10_000.0
    assert portfolio.total_return_pct() == 0.0

    # mark 涨到 110 → 持仓估值 110 → equity = 9900 + 110 = 10010
    portfolio.update_mark(_btc(), 110.0)
    assert portfolio.equity() == 10_010.0
    assert portfolio.total_return_pct() == 0.1


# ─── 反向开仓（先 long 后 short） ───


def test_full_round_trip_realized_pnl() -> None:
    stack = _build_stack(fee_rate=0.0)
    bus = cast(MessageBus, stack["bus"])
    exchange = cast(SimulatedExchange, stack["exchange"])
    portfolio = cast(Portfolio, stack["portfolio"])

    strat = _CapturingStrategy("s1", stack["clock"], bus)

    # 开多 1.0 @ 100
    o1 = Order(
        client_order_id=ClientOrderId("c-buy"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=1.0,
    )
    strat.submit_order(o1)
    exchange.process_bar(_make_bar(open=100.0, close=100.0, ts=1000))

    # 平多 1.0 @ 110
    o2 = Order(
        client_order_id=ClientOrderId("c-sell"),
        instrument_id=_btc(),
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=1.0,
    )
    strat.submit_order(o2)
    exchange.process_bar(_make_bar(open=110.0, close=110.0, ts=2000))

    pos = portfolio.position(_btc())
    assert pos is not None
    assert pos.is_flat
    assert pos.realized_pnl == 10.0  # (110-100) * 1.0

    # 现金：初始 10000 - 100（买）+ 110（卖）= 10010
    assert portfolio.cash == 10_010.0
    # equity = cash + 0 仓位 = 10010
    assert portfolio.equity() == 10_010.0


# ─── 拒单 ───


def test_unsupported_type_is_rejected_and_propagated() -> None:
    stack = _build_stack()
    bus = cast(MessageBus, stack["bus"])
    exchange = cast(SimulatedExchange, stack["exchange"])

    rejected_events: list[Any] = []
    bus.subscribe("events.order.s1", rejected_events.append)

    strat = _CapturingStrategy("s1", stack["clock"], bus)

    stop_order = Order(
        client_order_id=ClientOrderId("c-stop"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.STOP_MARKET,
        quantity=1.0,
    )
    strat.submit_order(stop_order)

    # 应该有 OrderSubmitted + OrderRejected
    types = [type(e).__name__ for e in rejected_events]
    assert "OrderSubmitted" in types
    assert "OrderRejected" in types
    assert stop_order.status == OrderStatus.REJECTED
    assert exchange.pending_count() == 0


# ─── 多策略隔离 ───


def test_two_strategies_isolated_events() -> None:
    stack = _build_stack()
    bus = cast(MessageBus, stack["bus"])
    exchange = cast(SimulatedExchange, stack["exchange"])

    a = _CapturingStrategy("strat-a", stack["clock"], bus)
    b = _CapturingStrategy("strat-b", stack["clock"], bus)

    a.submit_order(
        Order(
            client_order_id=ClientOrderId("a-1"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=1.0,
        )
    )

    exchange.process_bar(_make_bar(open=100.0, ts=1000))

    # a 收到，b 没收到
    assert len(a.fills) == 1
    assert len(b.fills) == 0

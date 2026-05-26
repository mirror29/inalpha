"""``Portfolio`` 接入 ``detect_close`` 后的 close 队列行为（ADR-0007 Slice 3）。

不依赖 DB / async worker。直接构造 Portfolio + 模拟 fill 验证：

- account_id=None 时不入队（向后兼容）
- account_id 提供时 close 入队
- partial / full / cross-zero 平仓的 staging 内容正确
- drain_closed_trades 幂等（一次清空，二次返空）
"""
from __future__ import annotations

from uuid import uuid4

from inalpha_paper.engine.portfolio import Portfolio
from inalpha_paper.kernel.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    VenueOrderId,
)
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.events import OrderFilled
from inalpha_paper.model.orders import OrderSide


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _fill(
    side: OrderSide,
    qty: float,
    price: float,
    *,
    ts: int = 1_700_000_000_000_000_000,
    client_order_id: str = "c-1",
    tag: str | None = None,
) -> OrderFilled:
    return OrderFilled(
        client_order_id=ClientOrderId(client_order_id),
        strategy_id=StrategyId("test"),
        ts_event=ts,
        ts_init=ts,
        venue_order_id=VenueOrderId("v-1"),
        instrument_id=_btc(),
        side=side,
        fill_quantity=qty,
        fill_price=price,
        trade_id="t-1",
        is_last_fill=True,
        tag=tag,
    )


def _emit(bus: MessageBus, evt: OrderFilled) -> None:
    bus.publish(f"events.fills.{evt.instrument_id}", evt)


# ─── 向后兼容：account_id=None 时不入队 ───


def test_no_account_id_no_close_queue() -> None:
    """account_id=None（默认）→ 不入队，drain 永远空。"""
    bus = MessageBus()
    portfolio = Portfolio(bus, initial_cash=10_000.0, fee_rate=0.0)

    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"))
    _emit(bus, _fill(OrderSide.SELL, 1.0, 110.0, client_order_id="close-1", tag="signal"))

    assert portfolio.drain_closed_trades() == []


# ─── account_id 提供时 close 入队 ───


def test_close_writes_to_queue() -> None:
    bus = MessageBus()
    account_id = uuid4()
    portfolio = Portfolio(
        bus, initial_cash=10_000.0, fee_rate=0.0, account_id=account_id
    )

    # 开仓 BUY 1 @ 100
    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"))
    assert portfolio.drain_closed_trades() == []  # 开仓不入队

    # 平仓 SELL 1 @ 110
    _emit(bus, _fill(
        OrderSide.SELL, 1.0, 110.0, client_order_id="close-1", tag="take_profit"
    ))

    staging_list = portfolio.drain_closed_trades()
    assert len(staging_list) == 1
    s = staging_list[0]
    assert s.account_id == account_id
    assert s.side == "long"
    assert s.venue == "binance"
    assert s.symbol == "BTC/USDT"
    assert s.exit_reason == "take_profit"
    assert s.open_order_id == "open-1"
    assert s.close_order_id == "close-1"
    assert float(s.quantity) == 1.0
    assert s.close_profit_abs == 10.0  # (110 - 100) × 1


def test_drain_is_idempotent() -> None:
    """drain 后队列空，再次 drain 返 []。"""
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)

    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"))
    _emit(bus, _fill(OrderSide.SELL, 1.0, 105.0, client_order_id="close-1"))

    first = portfolio.drain_closed_trades()
    assert len(first) == 1
    second = portfolio.drain_closed_trades()
    assert second == []


def test_no_tag_defaults_signal() -> None:
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)
    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"))
    _emit(bus, _fill(OrderSide.SELL, 1.0, 110.0, client_order_id="close-1", tag=None))

    staging = portfolio.drain_closed_trades()
    assert staging[0].exit_reason == "signal"


def test_partial_close_one_staging_per_close_part() -> None:
    """开仓 3 → 卖 1 → 卖 2（完全平）= 2 个 staging（每次 close 一条）。"""
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)

    _emit(bus, _fill(OrderSide.BUY, 3.0, 100.0, client_order_id="open-1"))
    _emit(bus, _fill(OrderSide.SELL, 1.0, 110.0, client_order_id="close-1"))
    _emit(bus, _fill(OrderSide.SELL, 2.0, 115.0, client_order_id="close-2"))

    staging = portfolio.drain_closed_trades()
    assert len(staging) == 2
    assert float(staging[0].quantity) == 1.0
    assert staging[0].close_profit_abs == 10.0  # (110-100)*1
    assert float(staging[1].quantity) == 2.0
    assert staging[1].close_profit_abs == 30.0  # (115-100)*2
    # 两次都用开仓时的 open_order_id
    assert staging[0].open_order_id == "open-1"
    assert staging[1].open_order_id == "open-1"


def test_cross_zero_reverse_open_only_close_part_enqueued() -> None:
    """long 1 → SELL 3：平 1 + 反向开 short 2。队列只有"平 1"。"""
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)

    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-long"))
    _emit(bus, _fill(OrderSide.SELL, 3.0, 110.0, client_order_id="cross-zero"))

    staging = portfolio.drain_closed_trades()
    assert len(staging) == 1
    assert float(staging[0].quantity) == 1.0
    assert staging[0].side == "long"
    # 反向开 short 后，再次平时 open_order_id 应是 cross-zero
    _emit(bus, _fill(OrderSide.BUY, 2.0, 100.0, client_order_id="close-short"))
    staging2 = portfolio.drain_closed_trades()
    assert len(staging2) == 1
    assert staging2[0].side == "short"
    assert staging2[0].open_order_id == "cross-zero"


def test_add_position_does_not_enqueue() -> None:
    """同向加仓不入队。"""
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)

    _emit(bus, _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"))
    _emit(bus, _fill(OrderSide.BUY, 1.0, 105.0, client_order_id="add-1"))

    assert portfolio.drain_closed_trades() == []

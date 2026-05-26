"""``ClosedTradesWriter`` worker（ADR-0007 Slice 4）。

Integration（pytest.mark.integration）：真 DB 端到端 Portfolio → writer → closed_trades 表。
单元测试 interval / batch 校验。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import inalpha_shared.db as shared_db
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.engine.close_detector import ClosedTradeStaging
from inalpha_paper.engine.closed_trades_writer import ClosedTradesWriter
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

pytestmark = pytest.mark.integration


def _get_pool() -> Any:
    if shared_db._pool is None:
        raise RuntimeError("DB pool not initialized")
    return shared_db._pool


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


@pytest_asyncio.fixture
async def clean_closed_trades_for_account(client: TestClient) -> AsyncIterator[UUID]:
    del client
    account_id = uuid4()
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM closed_trades WHERE account_id = %s", (str(account_id),)
        )
        await conn.commit()
    yield account_id
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM closed_trades WHERE account_id = %s", (str(account_id),)
        )
        await conn.commit()


# ─── 配置校验（单元，无 DB 依赖）───


def test_invalid_interval_rejected() -> None:
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4())
    with pytest.raises(ValueError, match="interval_seconds"):
        ClosedTradesWriter(portfolio, db_pool=None, interval_seconds=0)  # type: ignore[arg-type]


def test_invalid_batch_size_rejected() -> None:
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4())
    with pytest.raises(ValueError, match="batch_size"):
        ClosedTradesWriter(portfolio, db_pool=None, batch_size=0)  # type: ignore[arg-type]


# ─── 端到端 integration ───


@pytest.mark.asyncio
async def test_flush_writes_drained_staging_to_db(
    clean_closed_trades_for_account: UUID,
) -> None:
    """Portfolio → fill events → drain → writer → closed_trades 表。"""
    from inalpha_paper.storage import closed_trades as trades_store

    account_id = clean_closed_trades_for_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.BUY, 1.0, 100.0, client_order_id="open-1"),
    )
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(
            OrderSide.SELL, 1.0, 110.0, client_order_id="close-1", tag="take_profit"
        ),
    )

    writer = ClosedTradesWriter(portfolio, _get_pool(), interval_seconds=1.0)
    stats = await writer.flush_once()

    assert stats.inserted == 1
    assert stats.retry_buffered == 0
    # drain 完，再次 flush 0
    stats2 = await writer.flush_once()
    assert stats2.inserted == 0

    # DB 有 1 行
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=datetime(2020, 1, 1, tzinfo=UTC),
        )
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    assert row["venue"] == "binance"
    assert row["symbol"] == "BTC/USDT"
    assert row["side"] == "long"
    assert row["exit_reason"] == "take_profit"
    assert row["close_profit_abs"] == 10.0
    assert float(row["quantity"]) == 1.0
    assert row["open_order_id"] == "open-1"
    assert row["close_order_id"] == "close-1"


@pytest.mark.asyncio
async def test_flush_batches_many_closes(
    clean_closed_trades_for_account: UUID,
) -> None:
    """multi closes 分 batch 写。batch_size=5 + 12 close = 3 batch。"""
    from inalpha_paper.storage import closed_trades as trades_store

    account_id = clean_closed_trades_for_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    # 12 个开-平 cycle
    base_ts = 1_700_000_000_000_000_000
    for i in range(12):
        bus.publish(
            f"events.fills.{_btc()}",
            _fill(
                OrderSide.BUY, 1.0, 100.0,
                ts=base_ts + i * 2 * 1_000_000_000,
                client_order_id=f"open-{i}",
            ),
        )
        bus.publish(
            f"events.fills.{_btc()}",
            _fill(
                OrderSide.SELL, 1.0, 105.0,
                ts=base_ts + (i * 2 + 1) * 1_000_000_000,
                client_order_id=f"close-{i}",
            ),
        )

    writer = ClosedTradesWriter(
        portfolio, _get_pool(), interval_seconds=1.0, batch_size=5
    )
    stats = await writer.flush_once()
    assert stats.inserted == 12

    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=datetime(2020, 1, 1, tzinfo=UTC),
        )
    assert len(rows) == 12


@pytest.mark.asyncio
async def test_invalid_exit_reason_buffered_for_retry(
    clean_closed_trades_for_account: UUID,
) -> None:
    """非法 exit_reason 触发 DB CHECK violation → 进 retry buffer。"""
    account_id = clean_closed_trades_for_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    # 手动塞一条非法 staging（绕过 detect_close）
    portfolio._close_trade_queue.append(
        ClosedTradeStaging(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            open_ts=datetime(2026, 5, 26, 10, tzinfo=UTC),
            close_ts=datetime(2026, 5, 26, 11, tzinfo=UTC),
            open_price=Decimal("100"),
            close_price=Decimal("110"),
            quantity=Decimal("1"),
            close_profit_pct=0.1,
            close_profit_abs=10.0,
            exit_reason="INVALID_REASON_XYZ",  # CHECK 集合外
            open_order_id="o-1",
            close_order_id="c-1",
        )
    )

    writer = ClosedTradesWriter(portfolio, _get_pool(), interval_seconds=1.0)
    stats = await writer.flush_once()

    assert stats.inserted == 0
    assert stats.retry_buffered == 1


@pytest.mark.asyncio
async def test_run_forever_stops_on_event(
    clean_closed_trades_for_account: UUID,
) -> None:
    del clean_closed_trades_for_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=uuid4(), fee_rate=0.0)
    writer = ClosedTradesWriter(portfolio, _get_pool(), interval_seconds=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(writer.run_forever(stop))
    await asyncio.sleep(0.12)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()

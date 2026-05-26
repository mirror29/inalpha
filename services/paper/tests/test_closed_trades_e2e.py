"""ADR-0007 Slice 6 端到端集成：Portfolio → Writer → DB → PostgresTradeRepository → RiskRule。

跑一遍真实闭环：
1. Portfolio 接 fill events → 入 close 队列
2. ClosedTradesPipeline 后台 task → drain → 写 closed_trades 表 → refresh repo
3. CooldownRule 通过 PostgresTradeRepository 查 cache → 拦后续 submit
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import inalpha_shared.db as shared_db
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.engine.closed_trades_lifecycle import ClosedTradesPipeline
from inalpha_paper.engine.portfolio import Portfolio
from inalpha_paper.execution.risk_rules import CooldownRule
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
    ts: int,
    client_order_id: str,
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
async def clean_account(client: TestClient) -> AsyncIterator[UUID]:
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


# ─── 端到端 ───


@pytest.mark.asyncio
async def test_full_pipeline_close_appears_in_repo(clean_account: UUID) -> None:
    """开仓 → 平仓 → 一个 tick 后 repo 能看到这条 trade。"""
    account_id = clean_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    base_ts = 1_700_000_000_000_000_000
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.BUY, 1.0, 100.0, ts=base_ts, client_order_id="open-1"),
    )
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(
            OrderSide.SELL, 1.0, 110.0,
            ts=base_ts + 1_000_000_000,
            client_order_id="close-1",
            tag="take_profit",
        ),
    )

    pipeline = await ClosedTradesPipeline.start(
        portfolio, _get_pool(), account_id,
        writer_interval=0.1, repo_lookback_min=60,
    )
    try:
        # start() 内已 await flush + refresh；不需要 sleep
        # 验证 DB 真有这条 trade（fill ts 是 2023 年，超出 repo lookback 60min；
        # 但 DB 真写入了。下个 test 用真实近期 ts 验证 repo cache + RiskRule 闭环）
        from inalpha_paper.storage import closed_trades as trades_store

        async with get_conn() as conn:
            rows = await trades_store.list_recent(
                conn, account_id=account_id,
                close_after=datetime(2020, 1, 1, tzinfo=UTC),
            )
        assert len(rows) == 1
        row: dict[str, Any] = rows[0]
        assert row["exit_reason"] == "take_profit"
        assert row["close_profit_abs"] == 10.0
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_riskrule_blocks_after_recent_close(clean_account: UUID) -> None:
    """开仓 → 平仓（with 当下时刻 ts）→ CooldownRule 拦后续 submit。"""
    account_id = clean_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    # close_ts 用真实近期时刻让 cooldown 能命中
    now = datetime.now(UTC)
    open_ts = int((now - timedelta(minutes=10)).timestamp() * 1_000_000_000)
    close_ts = int((now - timedelta(minutes=2)).timestamp() * 1_000_000_000)

    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.BUY, 1.0, 100.0, ts=open_ts, client_order_id="open-1"),
    )
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.SELL, 1.0, 110.0, ts=close_ts, client_order_id="close-1"),
    )

    pipeline = await ClosedTradesPipeline.start(
        portfolio, _get_pool(), account_id,
        writer_interval=0.1,
        repo_lookback_min=60,  # 60min 窗口
    )
    try:
        await asyncio.sleep(0.3)

        cooldown = CooldownRule(
            {"stop_duration_min": 30, "lookback_min": 60},
            pipeline.trade_repo,
        )
        # 5 分钟内有平仓 → 拦
        verdict = cooldown.check_symbol(_btc(), now, "long", 10_000.0)
        assert verdict is not None
        assert verdict.rule_name == "CooldownRule"
        # 完整闭环：DB 真有数据
        async with get_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) AS cnt FROM closed_trades WHERE account_id = %s",
                (str(account_id),),
            )
            row = await cur.fetchone()
        cnt: dict[str, Any] = row  # type: ignore[assignment]
        assert cnt["cnt"] == 1
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_pipeline_stop_final_flush(clean_account: UUID) -> None:
    """stop() 触发最后 flush，确保数据不丢。"""
    from inalpha_paper.storage import closed_trades as trades_store

    account_id = clean_account
    bus = MessageBus()
    portfolio = Portfolio(bus, account_id=account_id, fee_rate=0.0)

    pipeline = await ClosedTradesPipeline.start(
        portfolio, _get_pool(), account_id, writer_interval=60.0,  # 长 interval
    )
    base_ts = 1_700_000_000_000_000_000
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.BUY, 1.0, 100.0, ts=base_ts, client_order_id="open-1"),
    )
    bus.publish(
        f"events.fills.{_btc()}",
        _fill(OrderSide.SELL, 1.0, 105.0, ts=base_ts + 1, client_order_id="close-1"),
    )

    # 不等 tick，立刻 stop（writer_interval=60s 不会自动 flush）
    await pipeline.stop()

    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=datetime(2020, 1, 1, tzinfo=UTC),
        )
    assert len(rows) == 1  # stop() 内部 final flush 写入了

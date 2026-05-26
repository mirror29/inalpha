"""``PostgresTradeRepository`` 单元 + integration（ADR-0007 Slice 5）。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import inalpha_shared.db as shared_db
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.execution.risk_rules import PostgresTradeRepository
from inalpha_paper.kernel.identifiers import InstrumentId


def _get_pool() -> Any:
    if shared_db._pool is None:
        raise RuntimeError("DB pool not initialized")
    return shared_db._pool


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _eth() -> InstrumentId:
    return InstrumentId(symbol="ETH/USDT", venue="binance")


# ─── 配置校验（单元）───


def test_invalid_lookback_min_rejected() -> None:
    with pytest.raises(ValueError, match="lookback_min"):
        PostgresTradeRepository(uuid4(), db_pool=None, lookback_min=0)  # type: ignore[arg-type]


def test_invalid_ttl_rejected() -> None:
    with pytest.raises(ValueError, match="cache_ttl_seconds"):
        PostgresTradeRepository(uuid4(), db_pool=None, cache_ttl_seconds=0)  # type: ignore[arg-type]


def test_empty_cache_returns_empty_list() -> None:
    """没 refresh 时 get_closed_trades 返空。"""
    repo = PostgresTradeRepository(uuid4(), db_pool=None)  # type: ignore[arg-type]
    rows = repo.get_closed_trades(close_after=datetime(2026, 1, 1, tzinfo=UTC))
    assert rows == []


# ─── Integration ───


pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture
async def clean_closed_trades(client: TestClient) -> AsyncIterator[UUID]:
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


async def _insert_trade(
    account_id: UUID,
    instrument: InstrumentId,
    close_ts: datetime,
    profit_pct: float,
    exit_reason: str = "signal",
    side: str = "long",
) -> None:
    from inalpha_paper.storage import closed_trades as trades_store

    async with get_conn() as conn:
        await trades_store.insert_close(
            conn,
            account_id=account_id,
            venue=instrument.venue,
            symbol=instrument.symbol,
            side=side,
            open_ts=close_ts - timedelta(minutes=10),
            close_ts=close_ts,
            open_price=Decimal("100"),
            close_price=Decimal("100"),
            quantity=Decimal("1"),
            close_profit_pct=profit_pct,
            close_profit_abs=profit_pct * 100,
            exit_reason=exit_reason,
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_refresh_loads_from_db(clean_closed_trades: UUID) -> None:
    account_id = clean_closed_trades
    now = datetime.now(UTC)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=5), 0.01)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=2), 0.02)

    repo = PostgresTradeRepository(account_id, _get_pool(), lookback_min=60)
    loaded = await repo.refresh(now)
    assert loaded == 2
    assert repo.cache_size == 2


@pytest.mark.asyncio
async def test_get_closed_trades_filters_by_instrument(
    clean_closed_trades: UUID,
) -> None:
    account_id = clean_closed_trades
    now = datetime.now(UTC)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=5), 0.01)
    await _insert_trade(account_id, _eth(), now - timedelta(minutes=3), 0.02)

    repo = PostgresTradeRepository(account_id, _get_pool(), lookback_min=60)
    await repo.refresh(now)

    btc_only = repo.get_closed_trades(
        instrument_id=_btc(),
        close_after=now - timedelta(minutes=60),
    )
    assert len(btc_only) == 1
    assert btc_only[0].instrument_id == _btc()


@pytest.mark.asyncio
async def test_get_closed_trades_filters_by_exit_reasons(
    clean_closed_trades: UUID,
) -> None:
    account_id = clean_closed_trades
    now = datetime.now(UTC)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=5), -0.02, "stop_loss")
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=4), 0.05, "take_profit")
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=3), -0.01, "liquidation")

    repo = PostgresTradeRepository(account_id, _get_pool(), lookback_min=60)
    await repo.refresh(now)

    stops = repo.get_closed_trades(
        close_after=now - timedelta(minutes=60),
        exit_reasons=["stop_loss", "trailing_stop_loss", "liquidation"],
    )
    assert len(stops) == 2
    reasons = sorted(t.exit_reason for t in stops)
    assert reasons == ["liquidation", "stop_loss"]


@pytest.mark.asyncio
async def test_get_closed_trades_filters_max_profit_pct(
    clean_closed_trades: UUID,
) -> None:
    """LowProfitRule 用法：只看 < 0 的 trade。"""
    account_id = clean_closed_trades
    now = datetime.now(UTC)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=10), -0.03)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=5), 0.02)

    repo = PostgresTradeRepository(account_id, _get_pool(), lookback_min=60)
    await repo.refresh(now)

    losers = repo.get_closed_trades(
        close_after=now - timedelta(minutes=60),
        max_profit_pct=0.0,
    )
    assert len(losers) == 1
    assert losers[0].close_profit_pct == -0.03


@pytest.mark.asyncio
async def test_account_isolation(clean_closed_trades: UUID) -> None:
    account_a = clean_closed_trades
    account_b = uuid4()
    now = datetime.now(UTC)
    await _insert_trade(account_a, _btc(), now - timedelta(minutes=5), 0.01)
    await _insert_trade(account_b, _btc(), now - timedelta(minutes=5), 0.99)

    repo_a = PostgresTradeRepository(account_a, _get_pool(), lookback_min=60)
    await repo_a.refresh(now)
    rows = repo_a.get_closed_trades(close_after=now - timedelta(minutes=60))

    # 清理 account_b
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM closed_trades WHERE account_id = %s", (str(account_b),)
        )
        await conn.commit()

    assert len(rows) == 1
    assert rows[0].close_profit_pct == 0.01


@pytest.mark.asyncio
async def test_refresh_clears_old_cache(clean_closed_trades: UUID) -> None:
    """refresh 覆盖式更新，不累加。"""
    account_id = clean_closed_trades
    now = datetime.now(UTC)
    await _insert_trade(account_id, _btc(), now - timedelta(minutes=5), 0.01)

    repo = PostgresTradeRepository(account_id, _get_pool(), lookback_min=60)
    await repo.refresh(now)
    assert repo.cache_size == 1

    # 删 DB 数据后再 refresh，cache 应清空
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM closed_trades WHERE account_id = %s", (str(account_id),)
        )
        await conn.commit()

    await repo.refresh(now)
    assert repo.cache_size == 0

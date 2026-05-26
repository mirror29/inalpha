"""`storage.closed_trades` PostgreSQL CRUD integration（ADR-0006 Step 4）。

依赖 alembic 0007 已在测试 DB 跑过。
覆盖 `insert_close` / `list_recent`（多种过滤）/ `count_by_account` + CHECK 约束验证。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.storage import closed_trades as trades_store

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def clean_closed_trades(client: TestClient) -> AsyncIterator[UUID]:
    """每个测试前后清空表 + 返回 unique account_id。"""
    del client
    account_id = uuid4()
    async with get_conn() as conn:
        await conn.execute("DELETE FROM closed_trades WHERE account_id = %s", (str(account_id),))
        await conn.commit()
    yield account_id
    async with get_conn() as conn:
        await conn.execute("DELETE FROM closed_trades WHERE account_id = %s", (str(account_id),))
        await conn.commit()


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ─── insert_close ───


@pytest.mark.asyncio
async def test_insert_returns_id(clean_closed_trades: UUID) -> None:
    account_id = clean_closed_trades
    async with get_conn() as conn:
        tid = await trades_store.insert_close(
            conn,
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            open_ts=_ts(2026, 5, 26, 10),
            close_ts=_ts(2026, 5, 26, 11),
            open_price=Decimal("50000.00"),
            close_price=Decimal("50500.00"),
            quantity=Decimal("0.1"),
            close_profit_pct=0.01,
            close_profit_abs=50.0,
            exit_reason="manual",
        )
        await conn.commit()
    assert isinstance(tid, int) and tid > 0


@pytest.mark.asyncio
async def test_insert_invalid_exit_reason_rejected(clean_closed_trades: UUID) -> None:
    """CHECK 约束拒未知 exit_reason。"""
    account_id = clean_closed_trades
    with pytest.raises(psycopg.errors.CheckViolation):
        async with get_conn() as conn:
            await trades_store.insert_close(
                conn,
                account_id=account_id,
                venue="binance",
                symbol="BTC/USDT",
                side="long",
                open_ts=_ts(2026, 5, 26, 10),
                close_ts=_ts(2026, 5, 26, 11),
                open_price=Decimal("100"),
                close_price=Decimal("100"),
                quantity=Decimal("1"),
                close_profit_pct=0.0,
                close_profit_abs=0.0,
                exit_reason="unknown_reason",  # 未在 CHECK 集合
            )
            await conn.commit()


@pytest.mark.asyncio
async def test_insert_invalid_side_rejected(clean_closed_trades: UUID) -> None:
    """CHECK 拒非 long/short side（如 BUY/SELL）。"""
    account_id = clean_closed_trades
    with pytest.raises(psycopg.errors.CheckViolation):
        async with get_conn() as conn:
            await trades_store.insert_close(
                conn,
                account_id=account_id,
                venue="binance",
                symbol="BTC/USDT",
                side="BUY",
                open_ts=_ts(2026, 5, 26, 10),
                close_ts=_ts(2026, 5, 26, 11),
                open_price=Decimal("100"),
                close_price=Decimal("100"),
                quantity=Decimal("1"),
                close_profit_pct=0.0,
                close_profit_abs=0.0,
                exit_reason="manual",
            )
            await conn.commit()


# ─── list_recent ───


async def _seed_trades(
    account_id: UUID,
    items: list[tuple[str, str, str, datetime, float, str]],
) -> None:
    """items: (venue, symbol, side, close_ts, profit_pct, exit_reason)。"""
    async with get_conn() as conn:
        for venue, symbol, side, close_ts, profit_pct, exit_reason in items:
            await trades_store.insert_close(
                conn,
                account_id=account_id,
                venue=venue,
                symbol=symbol,
                side=side,
                open_ts=close_ts - timedelta(hours=1),
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
async def test_list_recent_filter_by_close_after(
    clean_closed_trades: UUID,
) -> None:
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 8), 0.01, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 12), 0.02, "manual"),
        ],
    )
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn, account_id=account_id, close_after=_ts(2026, 5, 26, 10)
        )
    assert len(rows) == 1
    assert rows[0]["close_profit_pct"] == 0.02


@pytest.mark.asyncio
async def test_list_recent_filter_by_symbol(clean_closed_trades: UUID) -> None:
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), 0.01, "manual"),
            ("binance", "ETH/USDT", "long", _ts(2026, 5, 26, 11), 0.02, "manual"),
        ],
    )
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=_ts(2026, 5, 26, 0),
            venue="binance",
            symbol="BTC/USDT",
        )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_list_recent_filter_by_exit_reasons(clean_closed_trades: UUID) -> None:
    """StoplossGuardRule 用法：只看 stop_loss / trailing / liquidation。"""
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), -0.02, "stop_loss"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 11), 0.03, "take_profit"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 12), -0.04, "liquidation"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 13), 0.01, "manual"),
        ],
    )
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=_ts(2026, 5, 26, 0),
            exit_reasons=["stop_loss", "trailing_stop_loss", "liquidation"],
        )
    reasons = sorted(r["exit_reason"] for r in rows)
    assert reasons == ["liquidation", "stop_loss"]


@pytest.mark.asyncio
async def test_list_recent_filter_by_max_profit_pct(clean_closed_trades: UUID) -> None:
    """LowProfitRule 用法：只看亏损 trade。"""
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), -0.05, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 11), 0.02, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 12), -0.01, "manual"),
        ],
    )
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn,
            account_id=account_id,
            close_after=_ts(2026, 5, 26, 0),
            max_profit_pct=0.0,
        )
    assert len(rows) == 2
    profits = sorted(r["close_profit_pct"] for r in rows)
    assert profits == [-0.05, -0.01]


@pytest.mark.asyncio
async def test_list_recent_sorted_by_close_ts_asc(clean_closed_trades: UUID) -> None:
    """RiskRule.calculate_lock_end 要 max(close_ts)，调用方期望升序便于取最后一条。"""
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 12), 0.01, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), 0.01, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 11), 0.01, "manual"),
        ],
    )
    async with get_conn() as conn:
        rows = await trades_store.list_recent(
            conn, account_id=account_id, close_after=_ts(2026, 5, 26, 0)
        )
    timestamps = [r["close_ts"] for r in rows]
    assert timestamps == sorted(timestamps)


# ─── count_by_account ───


@pytest.mark.asyncio
async def test_count_by_account(clean_closed_trades: UUID) -> None:
    account_id = clean_closed_trades
    await _seed_trades(
        account_id,
        [
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 8), 0.01, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), 0.01, "manual"),
            ("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 12), 0.01, "manual"),
        ],
    )
    async with get_conn() as conn:
        total = await trades_store.count_by_account(
            conn, account_id=account_id, close_after=_ts(2026, 5, 26, 0)
        )
        recent = await trades_store.count_by_account(
            conn, account_id=account_id, close_after=_ts(2026, 5, 26, 11)
        )
    assert total == 3
    assert recent == 1


@pytest.mark.asyncio
async def test_account_isolation(clean_closed_trades: UUID) -> None:
    """不同 account_id 互不可见。"""
    account_a = clean_closed_trades
    account_b = uuid4()
    await _seed_trades(
        account_a,
        [("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 10), 0.01, "manual")],
    )
    await _seed_trades(
        account_b,
        [("binance", "BTC/USDT", "long", _ts(2026, 5, 26, 11), 0.02, "manual")],
    )
    async with get_conn() as conn:
        rows_a = await trades_store.list_recent(
            conn, account_id=account_a, close_after=_ts(2026, 5, 26, 0)
        )
        rows_b = await trades_store.list_recent(
            conn, account_id=account_b, close_after=_ts(2026, 5, 26, 0)
        )
        await conn.execute(
            "DELETE FROM closed_trades WHERE account_id = %s", (str(account_b),)
        )
        await conn.commit()
    a_data: dict[str, Any] = rows_a[0]
    b_data: dict[str, Any] = rows_b[0]
    assert a_data["close_profit_pct"] == 0.01
    assert b_data["close_profit_pct"] == 0.02

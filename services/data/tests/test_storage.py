"""bars 存储层集成测试 —— 真实 DB（docker postgres on 5433）。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from quant_lab_shared.db import get_conn

from quant_lab_data.storage.bars import count_bars, insert_bars, query_bars

pytestmark = pytest.mark.integration


@pytest.mark.usefixtures("db_pool")
async def test_insert_and_query_round_trip(venue_symbol_tf: tuple[str, str, str]) -> None:
    venue, symbol, tf = venue_symbol_tf
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        (base, 100.0, 101.0, 99.0, 100.5, 1000.0),
        (base.replace(hour=1), 100.5, 102.0, 100.0, 101.5, 1100.0),
        (base.replace(hour=2), 101.5, 103.0, 101.0, 102.5, 1200.0),
    ]

    async with get_conn() as conn:
        n = await insert_bars(conn, venue, symbol, tf, bars)
        assert n == 3

        rows = await query_bars(
            conn,
            venue=venue,
            symbol=symbol,
            timeframe=tf,
            from_ts=base,
            to_ts=base.replace(hour=23),
        )
        assert len(rows) == 3
        assert [float(r["close"]) for r in rows] == [100.5, 101.5, 102.5]
        assert [float(r["volume"]) for r in rows] == [1000.0, 1100.0, 1200.0]


@pytest.mark.usefixtures("db_pool")
async def test_insert_is_idempotent(venue_symbol_tf: tuple[str, str, str]) -> None:
    """重复 insert 触发 ON CONFLICT DO UPDATE，最终值是后写入的。"""
    venue, symbol, tf = venue_symbol_tf
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    async with get_conn() as conn:
        await insert_bars(conn, venue, symbol, tf, [(ts, 100.0, 101.0, 99.0, 100.5, 1000.0)])
        await insert_bars(conn, venue, symbol, tf, [(ts, 200.0, 201.0, 199.0, 200.5, 2000.0)])

        rows = await query_bars(conn, venue=venue, symbol=symbol, timeframe=tf,
                                from_ts=ts, to_ts=ts)
        assert len(rows) == 1
        assert float(rows[0]["close"]) == 200.5
        assert float(rows[0]["volume"]) == 2000.0


@pytest.mark.usefixtures("db_pool")
async def test_query_filters_time_range(venue_symbol_tf: tuple[str, str, str]) -> None:
    venue, symbol, tf = venue_symbol_tf
    base = datetime(2026, 2, 1, tzinfo=UTC)
    bars = [
        (base.replace(hour=h), 100.0, 101.0, 99.0, 100.5, 1000.0)
        for h in range(6)
    ]

    async with get_conn() as conn:
        await insert_bars(conn, venue, symbol, tf, bars)

        rows = await query_bars(
            conn, venue=venue, symbol=symbol, timeframe=tf,
            from_ts=base.replace(hour=2),
            to_ts=base.replace(hour=4),
        )
        assert len(rows) == 3  # hour 2, 3, 4 都在闭区间内


@pytest.mark.usefixtures("db_pool")
async def test_count_bars(venue_symbol_tf: tuple[str, str, str]) -> None:
    venue, symbol, tf = venue_symbol_tf
    base = datetime(2026, 3, 1, tzinfo=UTC)
    bars = [(base.replace(hour=h), 1.0, 1.0, 1.0, 1.0, 1.0) for h in range(4)]

    async with get_conn() as conn:
        await insert_bars(conn, venue, symbol, tf, bars)

        # 全部
        assert await count_bars(conn, venue, symbol, tf) == 4
        # 限定时段
        assert await count_bars(conn, venue, symbol, tf,
                                from_ts=base.replace(hour=1),
                                to_ts=base.replace(hour=2)) == 2

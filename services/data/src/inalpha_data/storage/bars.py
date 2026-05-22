"""bars hypertable 的读写。

表 schema 见 ``infra/migrations/versions/0001_initial_schema.py``：

- PK: (ts, venue, symbol, timeframe)
- 二级索引：(venue, symbol, timeframe, ts DESC)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

BarRow = tuple[datetime, float, float, float, float, float]
"""(ts, open, high, low, close, volume)"""


async def insert_bars(
    conn: AsyncConnection,
    venue: str,
    symbol: str,
    timeframe: str,
    bars: list[BarRow],
) -> int:
    """批量写入。``ON CONFLICT`` 走 ``DO UPDATE`` 让 backfill 幂等。

    Returns:
        实际触发的行数（INSERT 或 UPDATE 都算）。
    """
    if not bars:
        return 0

    rows = [(ts, venue, symbol, timeframe, o, h, low, c, v) for ts, o, h, low, c, v in bars]

    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO bars (ts, venue, symbol, timeframe, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, venue, symbol, timeframe) DO UPDATE
              SET open   = EXCLUDED.open,
                  high   = EXCLUDED.high,
                  low    = EXCLUDED.low,
                  close  = EXCLUDED.close,
                  volume = EXCLUDED.volume
            """,
            rows,
        )
        # psycopg executemany 后 rowcount 是最后一次执行的 rowcount；
        # 我们不依赖它做精确统计，直接返回输入行数
    await conn.commit()
    return len(rows)


async def query_bars(
    conn: AsyncConnection,
    venue: str,
    symbol: str,
    timeframe: str,
    from_ts: datetime,
    to_ts: datetime,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """查询给定时段的 K 线，按时间正序返回。

    **当时段内 bar 总数 > limit 时取最新 N 根**（不是最早 N 根）——
    这是 caller 的直觉默认（"我要取近期 100 根" / "data.get_bars(limit=1) 拿最新价"）。
    服务端用 ``ORDER BY ts DESC LIMIT N`` 取尾部，再外层 ``ORDER BY ts ASC`` 反转成
    时间正序返回（策略 / 回测都要时间正序消费）。

    返回 dict 列表（psycopg dict_row factory 决定）。键：
    ``ts, venue, symbol, timeframe, open, high, low, close, volume``。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH recent AS (
              SELECT ts, venue, symbol, timeframe, open, high, low, close, volume
              FROM bars
              WHERE venue = %s
                AND symbol = %s
                AND timeframe = %s
                AND ts >= %s
                AND ts <= %s
              ORDER BY ts DESC
              LIMIT %s
            )
            SELECT * FROM recent ORDER BY ts ASC
            """,
            (venue, symbol, timeframe, from_ts, to_ts, limit),
        )
        rows = await cur.fetchall()
    # dict_row 工厂保证每行是 dict[str, Any]；mypy 看不到 row_factory 的类型传导
    return list(rows)  # type: ignore[arg-type]


async def count_bars(
    conn: AsyncConnection,
    venue: str,
    symbol: str,
    timeframe: str,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> int:
    """统计某段 K 线条数（测试 / 监控用）。"""
    sql = "SELECT COUNT(*) AS n FROM bars WHERE venue = %s AND symbol = %s AND timeframe = %s"
    params: list[Any] = [venue, symbol, timeframe]
    if from_ts is not None:
        sql += " AND ts >= %s"
        params.append(from_ts)
    if to_ts is not None:
        sql += " AND ts <= %s"
        params.append(to_ts)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        row = await cur.fetchone()
    # dict_row 工厂保证 row 是 dict；mypy 看不到 row_factory 的类型传导
    return int(row["n"]) if row else 0  # type: ignore[call-overload]

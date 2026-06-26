"""constituent_snapshot 读写（#106 / ADR-0053 阶段 C · 指数成分 PIT 快照）。

表 schema 见 ``infra/migrations/versions/0022_constituent_snapshot.py``:
- UNIQUE (index_code, constituent_code, as_of_date)
- 索引 (index_code, as_of_date DESC) 给 time-travel 查询

数据源现实(实测):免费历史 PIT 成分拿不到 → 从今天起每日快照当前成分、向前累积;
time-travel 取 ``as_of_date <= 目标`` 的最近一份,早于最早快照 → 空(上层标 non-PIT 降级)。

连接用 ``dict_row`` 工厂(inalpha_shared.db)——行是 dict,按列名取。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import AsyncConnection


async def upsert_snapshot(
    conn: AsyncConnection,
    *,
    index_code: str,
    as_of_date: date,
    constituents: list[dict[str, Any]],
) -> int:
    """把某 index 在 ``as_of_date`` 的成分全量写入；同日重录幂等（ON CONFLICT DO UPDATE）。

    Args:
        constituents: ``[{code, name?, weight?}]``。
    Returns:
        写入/更新的行数。
    """
    rows = [
        (index_code, c["code"], c.get("name"), c.get("weight"), as_of_date)
        for c in constituents
        if c.get("code")
    ]
    if not rows:
        return 0
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO constituent_snapshot
                (index_code, constituent_code, name, weight, as_of_date)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (index_code, constituent_code, as_of_date)
            DO UPDATE SET name = EXCLUDED.name, weight = EXCLUDED.weight
            """,
            rows,
        )
    return len(rows)


async def get_constituents(
    conn: AsyncConnection,
    *,
    index_code: str,
    as_of: date,
) -> tuple[date | None, list[dict[str, Any]]]:
    """time-travel:返回 ``as_of_date <= as_of`` 的**最近一份**快照成分。

    Returns:
        ``(snapshot_date, [{code, name, weight}])`` —— snapshot_date 是实际命中的快照日;
        无 <= as_of 的快照（早于最早快照 / 该 index 从没快照过）→ ``(None, [])``，
        由上层标 ``is_pit=false`` 显式降级（§3.1，不静默假装 PIT）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT max(as_of_date) AS snap FROM constituent_snapshot "
            "WHERE index_code = %s AND as_of_date <= %s",
            (index_code, as_of),
        )
        # dict_row 工厂保证行是 dict；mypy 看不到 row_factory 的类型传导（同 storage/bars.py）
        row: dict[str, Any] | None = await cur.fetchone()  # type: ignore[assignment]
        snap_date = row["snap"] if row else None
        if snap_date is None:
            return None, []
        await cur.execute(
            "SELECT constituent_code, name, weight FROM constituent_snapshot "
            "WHERE index_code = %s AND as_of_date = %s ORDER BY constituent_code",
            (index_code, snap_date),
        )
        members: list[dict[str, Any]] = await cur.fetchall()  # type: ignore[assignment]
    return snap_date, [
        {
            "code": m["constituent_code"],
            "name": m["name"],
            "weight": float(m["weight"]) if m["weight"] is not None else None,
        }
        for m in members
    ]

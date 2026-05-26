"""risk_locks 表读写（D-9 · ADR-0006）。

设计约定（同 storage 其他模块）：
- 所有函数接 ``AsyncConnection`` 参数（不内部 acquire），让调用方控制事务
- 返回 ``dict[str, Any]``（DB row 风格，dict_row）

被 `services/paper` 同步路径（msgbus callback）调不了——本模块异步 API 供：
1. 后台 reconcile worker（把 InMemoryLockStore 状态周期性 dump 进 DB）
2. FastAPI 路由 / Mastra MCP tool 查询 active locks
3. 人工 unlock UI
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection


async def insert(
    conn: AsyncConnection,
    *,
    scope: str,
    rule_name: str,
    reason: str,
    locked_until: datetime,
    market: str | None = None,
    symbol: str | None = None,
    side: str = "*",
) -> int:
    """写一行 lock。返回新 id。

    Args:
        scope: 'global' / 'market' / 'symbol'
        market: scope='market' 必填；scope='symbol' 可选（便于按 market 聚合）
        symbol: scope='symbol' 必填
        side: 'long' / 'short' / '*'
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO risk_locks (
                scope, market, symbol, side, rule_name, reason, locked_until
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
            """,
            (scope, market, symbol, side, rule_name, reason, locked_until),
        )
        row = await cur.fetchone()
    return int(row["id"])  # type: ignore[index]


async def list_active(
    conn: AsyncConnection,
    *,
    now: datetime,
    scope: str | None = None,
    market: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出 `now` 时刻仍有效的 lock（active=TRUE 且 locked_until > now）。

    按 locked_until DESC 排（解锁时间最远的先看）。
    """
    sql = (
        "SELECT id, scope, market, symbol, side, rule_name, reason, "
        "locked_at, locked_until FROM risk_locks "
        "WHERE active = TRUE AND locked_until > %s"
    )
    params: list[Any] = [now]
    if scope is not None:
        sql += " AND scope = %s"
        params.append(scope)
    if market is not None:
        sql += " AND market = %s"
        params.append(market)
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    sql += " ORDER BY locked_until DESC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def is_locked(
    conn: AsyncConnection,
    *,
    now: datetime,
    scope: str,
    market: str | None = None,
    symbol: str | None = None,
    side: str = "*",
) -> dict[str, Any] | None:
    """精确查 `now` 时 ``scope`` + ``market``/``symbol`` 是否被锁。

    Side 兼容语义（同 `InMemoryLockStore._side_intersects`）：
    - 锁的 ``side='*'`` 拦任何方向查询
    - 查询的 ``side='*'`` 命中任何方向的锁
    - 否则 long 锁拦 long 查询、short 锁拦 short 查询，不互拦

    Returns:
        首个命中锁的 row dict（按 locked_until DESC，最远解锁的先看）；无锁返 ``None``。

    Note:
        scope='global' 时忽略 market/symbol 参数；scope='market' 时 symbol 字段必须为 NULL；
        scope='symbol' 时按 symbol 精确匹配。
    """
    sql = (
        "SELECT id, scope, market, symbol, side, rule_name, reason, "
        "locked_at, locked_until FROM risk_locks "
        "WHERE active = TRUE AND locked_until > %s AND scope = %s"
    )
    params: list[Any] = [now, scope]

    if scope == "global":
        # global 锁不区分 market/symbol；这两个字段在 DB 里也是 NULL
        pass
    elif scope == "market":
        if market is None:
            return None
        sql += " AND market = %s AND symbol IS NULL"
        params.append(market)
    elif scope == "symbol":
        if symbol is None:
            return None
        sql += " AND symbol = %s"
        params.append(symbol)
    else:
        raise ValueError(f"invalid scope {scope!r}")

    # side 兼容：锁的 side='*' 或等于查询；查询的 side='*' 匹配任何锁
    if side == "*":
        # 查询双向 → 任何锁都命中
        pass
    else:
        sql += " AND (side = '*' OR side = %s)"
        params.append(side)

    sql += " ORDER BY locked_until DESC LIMIT 1"

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        row = await cur.fetchone()
    return dict(row) if row else None  # type: ignore[arg-type]


async def manual_unlock(
    conn: AsyncConnection,
    lock_id: int,
    *,
    unlocked_by: str,
    unlock_reason: str,
) -> bool:
    """人工 unlock。软删（active=FALSE + 写入 unlock_at / unlocked_by / unlock_reason）。

    Returns:
        True 如果有行被改；False 如果 lock_id 不存在或已 inactive。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE risk_locks
            SET active = FALSE,
                unlocked_at = NOW(),
                unlocked_by = %s,
                unlock_reason = %s
            WHERE id = %s AND active = TRUE
            """,
            (unlocked_by, unlock_reason, lock_id),
        )
        return cur.rowcount > 0


async def expire_past_locks(conn: AsyncConnection, *, now: datetime) -> int:
    """把已到 locked_until 的 lock 自动 expire（active=FALSE）。

    后台 reconcile worker 定期调，避免 list_active 过滤大量已 expire 行。
    返回被 expire 的数量。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE risk_locks
            SET active = FALSE,
                unlocked_by = 'system',
                unlock_reason = 'expired',
                unlocked_at = NOW()
            WHERE active = TRUE AND locked_until <= %s
            """,
            (now,),
        )
        return cur.rowcount

"""psycopg 异步连接池。

各 service FastAPI 启动时调 :func:`init_pool`，关闭时调 :func:`close_pool`。
路由里用 :data:`DBConn` 类型注解（FastAPI dependency）拿到连接。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


def _normalize_url(url: str) -> str:
    """SQLAlchemy 风格 ``postgresql+psycopg://`` → libpq 风格 ``postgresql://``。"""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def init_pool(
    database_url: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
    timeout: float = 30.0,  # noqa: ASYNC109  传给 psycopg 池构造器，不是 asyncio 超时
) -> AsyncConnectionPool:
    """初始化全局连接池。各 service 在 startup 时调一次。"""
    global _pool
    if _pool is not None:
        raise RuntimeError("DB pool already initialized")

    _pool = AsyncConnectionPool(
        conninfo=_normalize_url(database_url),
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await _pool.open(wait=True)
    return _pool


async def close_pool() -> None:
    """关闭全局连接池。各 service 在 shutdown 时调一次。"""
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[AsyncConnection]:
    """背景任务 / 非 FastAPI 上下文里拿连接。"""
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() first")
    async with _pool.connection() as conn:
        yield conn


async def _db_dep() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency 内部实现。"""
    async with get_conn() as conn:
        yield conn


DBConn = Annotated[AsyncConnection, Depends(_db_dep)]
"""FastAPI 路由参数类型注解。

用法::

    from quant_lab_shared.db import DBConn

    @app.get("/strategies")
    async def list_strategies(db: DBConn):
        async with db.cursor() as cur:
            await cur.execute("SELECT id, name FROM strategies")
            return await cur.fetchall()
"""


async def get_db() -> AsyncIterator[AsyncConnection]:
    """不用 ``Annotated`` 的别名（兼容老风格 ``Depends(get_db)``）。"""
    async with get_conn() as conn:
        yield conn

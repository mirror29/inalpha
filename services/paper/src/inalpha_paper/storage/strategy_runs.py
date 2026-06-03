"""strategy_runs 表读写 —— live runner 状态机（D-11 issue #1）。

一行 = 一个 promoted candidate 在某市场按某 timeframe 自动跑的 live 状态。状态
``running → stopped``（用户 stop）/ ``running → errored``（连续错或服务重启 reconcile）。
``UNIQUE(candidate_id) WHERE status='running'`` 在 DB 层硬保证同 candidate 同时只一个
running——并发 insert 第二个会撞 UniqueViolation，转成 :class:`StrategyRunConflict`。
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from inalpha_shared.errors import InalphaError
from psycopg import AsyncConnection
from psycopg import errors as pg_errors

_RUNNING = "running"


class StrategyRunConflict(InalphaError):
    """同 candidate 已有一个 running 的 run（撞部分唯一索引）。"""

    code = "STRATEGY_RUN_ALREADY_RUNNING"
    status_code = 409


async def insert(
    conn: AsyncConnection,
    *,
    candidate_id: UUID,
    account_id: UUID,
    venue: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建一行 status='running' 的 run。同 candidate 已有 running → StrategyRunConflict。"""
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO strategy_runs (
                    candidate_id, account_id, status, venue, symbol, timeframe, params
                ) VALUES (%s, %s, 'running', %s, %s, %s, %s::jsonb)
                RETURNING id, candidate_id, account_id, status, venue, symbol,
                          timeframe, params, last_bar_ts, cumulative_pnl, error_log,
                          started_at, stopped_at
                """,
                (
                    str(candidate_id), str(account_id), venue, symbol, timeframe,
                    json.dumps(params or {}),
                ),
            )
            row = await cur.fetchone()
    except pg_errors.UniqueViolation as e:
        raise StrategyRunConflict(
            f"candidate {candidate_id} already has a running strategy_run",
            details={"candidate_id": str(candidate_id)},
        ) from e
    if row is None:  # 理论不会
        raise RuntimeError("strategy_runs insert returned no row")
    return row  # type: ignore[return-value]


async def get(conn: AsyncConnection, run_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, candidate_id, account_id, status, venue, symbol, timeframe, "
            "params, last_bar_ts, cumulative_pnl, error_log, started_at, stopped_at "
            "FROM strategy_runs WHERE id = %s",
            (str(run_id),),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def list_by_account(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, candidate_id, account_id, status, venue, symbol, timeframe, "
        "params, last_bar_ts, cumulative_pnl, error_log, started_at, stopped_at "
        "FROM strategy_runs WHERE account_id = %s"
    )
    args: list[Any] = [str(account_id)]
    if status is not None:
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY started_at DESC"
    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(args))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def set_status(
    conn: AsyncConnection,
    run_id: UUID,
    status: str,
) -> dict[str, Any] | None:
    """切状态；离开 running（stopped/errored）时记 stopped_at。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_runs
            SET status = %s,
                stopped_at = CASE WHEN %s <> 'running' THEN NOW() ELSE stopped_at END
            WHERE id = %s
            RETURNING id, candidate_id, account_id, status, venue, symbol, timeframe,
                      params, last_bar_ts, cumulative_pnl, error_log, started_at, stopped_at
            """,
            (status, status, str(run_id)),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def update_progress(
    conn: AsyncConnection,
    run_id: UUID,
    *,
    last_bar_ts: datetime,
    cumulative_pnl: Decimal,
) -> None:
    """记录已处理到的最新 bar + 累计 pnl。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE strategy_runs SET last_bar_ts = %s, cumulative_pnl = %s WHERE id = %s",
            (last_bar_ts, cumulative_pnl, str(run_id)),
        )


async def append_error_log(conn: AsyncConnection, run_id: UUID, error: str) -> None:
    """往 error_log JSONB 数组追加一条 ``{ts, error}``。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_runs
            SET error_log = error_log || jsonb_build_array(
                    jsonb_build_object('ts', NOW()::text, 'error', %s::text)
                )
            WHERE id = %s
            """,
            (error, str(run_id)),
        )


async def mark_running_as_errored(conn: AsyncConnection, *, reason: str) -> int:
    """把所有 running 行标 errored（服务重启 reconcile：内存 task 已丢失）。返回受影响行数。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_runs
            SET status = 'errored',
                stopped_at = NOW(),
                error_log = error_log || jsonb_build_array(
                    jsonb_build_object('ts', NOW()::text, 'error', %s::text)
                )
            WHERE status = %s
            """,
            (reason, _RUNNING),
        )
        return cur.rowcount


# ─── 复盘决策日志（D-11 issue #1）───


async def insert_decision(
    conn: AsyncConnection,
    *,
    run_id: UUID,
    bar_ts: datetime,
    bar_close: Decimal,
    side: str,
    quantity: Decimal,
    order_type: str,
    outcome: str,
    intent: str | None = None,
    limit_price: Decimal | None = None,
    tag: str | None = None,
    fill_price: Decimal | None = None,
    fee: Decimal | None = None,
    plan_id: UUID | None = None,
    order_id: str | None = None,
    reason: str | None = None,
) -> None:
    """记一行决策事件（策略在某根 bar 产生下单意图 + 撮合结果），供复盘。

    ``intent``：open_long / open_short / close（按下单前持仓方向 + side 判），补 side
    缺失的做多/做空语义。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO strategy_run_decisions (
                run_id, bar_ts, bar_close, side, quantity, order_type, limit_price,
                tag, intent, outcome, fill_price, fee, plan_id, order_id, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(run_id), bar_ts, bar_close, side, quantity, order_type, limit_price,
                tag, intent, outcome, fill_price, fee,
                str(plan_id) if plan_id is not None else None, order_id, reason,
            ),
        )


async def list_decisions(
    conn: AsyncConnection,
    run_id: UUID,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """按时间顺序列出某 run 的决策时间线（复盘用）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, run_id, bar_ts, bar_close, side, quantity, order_type, limit_price,
                   tag, intent, outcome, fill_price, fee, plan_id, order_id, reason, created_at
            FROM strategy_run_decisions
            WHERE run_id = %s
            ORDER BY created_at, id
            LIMIT %s
            """,
            (str(run_id), limit),
        )
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]

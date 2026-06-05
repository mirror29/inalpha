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
    limit: int = 200,
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
    # 兜底上限:run 历史会随时间无界增长，dashboard 每 6s 轮询全量会越来越重。
    # 按 started_at DESC 取最近 limit 条（最新的 run 最相关）。
    sql += " ORDER BY started_at DESC LIMIT %s"
    args.append(limit)
    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(args))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def count_running_by_account(conn: AsyncConnection, account_id: UUID) -> int:
    """当前账户 status='running' 的 run 数量（per-account 上限校验 issue #36.2）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COUNT(*) AS n FROM strategy_runs "
            "WHERE account_id = %s AND status = %s",
            (str(account_id), _RUNNING),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


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
    cumulative_pnl: Decimal | None = None,
) -> None:
    """记录已处理到的最新 bar + 累计 pnl。

    ``cumulative_pnl=None`` 时只推进 last_bar_ts、**保留**旧 pnl 值——FX 不可用
    （非 USD symbol 折算失败）时不拿错值覆盖正确旧值（issue #45）。
    """
    async with conn.cursor() as cur:
        if cumulative_pnl is None:
            await cur.execute(
                "UPDATE strategy_runs SET last_bar_ts = %s WHERE id = %s",
                (last_bar_ts, str(run_id)),
            )
        else:
            await cur.execute(
                "UPDATE strategy_runs SET last_bar_ts = %s, cumulative_pnl = %s WHERE id = %s",
                (last_bar_ts, cumulative_pnl, str(run_id)),
            )


async def append_error_log(
    conn: AsyncConnection, run_id: UUID, error: str, *, code: str | None = None
) -> None:
    """往 error_log JSONB 数组追加一条 ``{ts, error, code}``（``code`` 为错误分类，issue #41）。

    ``code`` 为 ``None`` 时写 JSON ``null``（结构仍统一）；build 阶段的可重试 / 不可重试
    分类见 ``live_runner._classify_build_error``（infra_unavailable / strategy_error / unknown）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_runs
            SET error_log = error_log || jsonb_build_array(
                    jsonb_build_object('ts', NOW()::text, 'error', %s::text, 'code', %s::text)
                )
            WHERE id = %s
            """,
            (error, code, str(run_id)),
        )


async def list_all_running(conn: AsyncConnection) -> list[dict[str, Any]]:
    """列出全表 status='running' 的 run（lifespan resume 用，issue #46）。

    单实例 MVP：进程独占，启动时全部 running 都是本进程上一生命周期的残留 → 全部 resume。
    多实例横向扩展时需按 runner_instance_id 限定作用域（#38.1），那之前别多副本跑。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, candidate_id, account_id, status, venue, symbol, timeframe, "
            "params, last_bar_ts, cumulative_pnl, error_log, started_at, stopped_at "
            "FROM strategy_runs WHERE status = %s ORDER BY started_at",
            (_RUNNING,),
        )
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


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

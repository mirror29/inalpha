"""backtest_runs 表读写（D-8c 起，落库 + 血缘追踪）。

行为约定：
- 每次跑完回测都写一行（status='done'），含 metrics + config + 血缘
- ``params_hash`` 用 sha256(strategy_code + JSON-sorted(params)) 计算，去重 / 比对
- 失败的回测 status='failed' + error；本 MVP 只写 done（失败用 HTTP 错误返回）

参考 ``orders.py`` 的事务约定：调用方持有 AsyncConnection，本模块只发 SQL。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection


def compute_params_hash(strategy_code: str, params: dict[str, Any]) -> str:
    """sha256(strategy_code|json.dumps(params, sort_keys=True))，截断 16 位 hex。

    用途：判断"同 strategy + 同 params"是否已跑过回测；orchestration 层可借此
    避免重复计算。
    """
    payload = strategy_code + "|" + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def insert_run(
    conn: AsyncConnection,
    *,
    strategy_code: str,
    config: dict[str, Any],
    metrics: dict[str, Any],
    status: str = "done",
    research_id: UUID | None = None,
    strategy_hint: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    created_by: UUID | None = None,
    account_id: str | None = None,
) -> UUID:
    """落一行 backtest_runs，返回生成的 run_id。

    Args:
        strategy_code: 策略注册表 key（'sma_cross' / 'mean_reversion' / 'buy_and_hold'）
        config: 回测请求体（venue/symbol/timeframe/from_ts/to_ts/params/initial_cash/fee_rate）
        metrics: 回测产出指标（sharpe/sortino/max_drawdown/win_rate/total_return_pct...）
        status: 'done' / 'failed' / etc，CHECK 约束见 migration 0001
        research_id: 触发本次回测的 research 产物 ID（可空）
        strategy_hint: 触发本次回测的原始 strategy_hint dict（审计用）
        account_id: 账户归属(migration 0025 补列,与 strategy_runs.account_id 同源)"""
    run_id = uuid4()
    params = config.get("params") or {}
    params_hash = compute_params_hash(strategy_code, params)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO backtest_runs (
                id, strategy_id, strategy_code, config, status, metrics,
                research_id, params_hash, strategy_hint,
                started_at, finished_at, created_by, account_id
            ) VALUES (
                %s, NULL, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                str(run_id),
                strategy_code,
                json.dumps(config, default=str),
                status,
                json.dumps(metrics, default=str),
                str(research_id) if research_id else None,
                params_hash,
                json.dumps(strategy_hint, default=str) if strategy_hint else None,
                started_at,
                finished_at,
                str(created_by) if created_by else None,
                account_id,
            ),
        )
    return run_id


async def list_by_research(
    conn: AsyncConnection,
    research_id: UUID,
    *,
    limit: int = 20,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """按 research_id 拉历史回测（按 created_at DESC）,可选按 account_id 过滤。

    返回 dict list，含 id/strategy_code/config/metrics/params_hash/created_at。
    """
    where = "WHERE research_id = %s"
    params: list[Any] = [str(research_id)]
    if account_id:
        where += " AND account_id = %s"
        params.append(account_id)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, strategy_code, config, metrics, params_hash,
                   research_id, strategy_hint, created_at, status
            FROM backtest_runs
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (*params, limit),
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def list_by_strategy(
    conn: AsyncConnection,
    strategy_code: str,
    *,
    limit: int = 20,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """按 strategy_code 拉历史回测（按 created_at DESC）,可选按 account_id 过滤。"""
    where = "WHERE strategy_code = %s"
    params: list[Any] = [strategy_code]
    if account_id:
        where += " AND account_id = %s"
        params.append(account_id)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, strategy_code, config, metrics, params_hash,
                   research_id, strategy_hint, created_at, status
            FROM backtest_runs
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (*params, limit),
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_by_id(
    conn: AsyncConnection,
    run_id: UUID,
) -> dict[str, Any] | None:
    """按 run_id 查单行；查不到返 None。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, strategy_code, config, metrics, params_hash,
                   research_id, strategy_hint, created_at, status
            FROM backtest_runs
            WHERE id = %s
            """,
            (str(run_id),),
        )
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    """psycopg dict_row → 简化 dict（与 paper storage 其它模块一致）。

    JSONB 列已 decode 成 Python dict / list；UUID 列保留为 UUID 对象。
    """
    return {
        "id": row["id"],
        "strategy_code": row["strategy_code"],
        "config": row["config"],
        "metrics": row["metrics"],
        "params_hash": row["params_hash"],
        "research_id": row["research_id"],
        "strategy_hint": row["strategy_hint"],
        "created_at": row["created_at"],
        "status": row["status"],
    }

async def list_recent(
    conn: AsyncConnection,
    *,
    limit: int = 20,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """按 account_id(若给)查最近回测（按 created_at DESC）,供活动流/策略实验室。"""
    where = "WHERE account_id = %s" if account_id else "WHERE 1=1"
    params: list[Any] = [account_id] if account_id else []
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, strategy_code, config, metrics, params_hash,
                   research_id, strategy_hint, created_at, status
            FROM backtest_runs
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (*params, limit),
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]

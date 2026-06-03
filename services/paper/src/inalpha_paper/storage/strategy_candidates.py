"""strategy_candidates 表读写（D-9 · ADR-0020 E1 MVP）。

行为约定：

- ``insert_candidate`` 计算 ``code_hash`` = sha256(code) 前 16 hex；UNIQUE 撞了
  返回已有 candidate（不抛 ——LLM 经常会写一模一样的策略，幂等）
- ``update_after_backtest`` 写最近一次 metrics / fitness / backtest_run_id；
  ``updated_at`` 自动刷新
- ``set_status`` 改 status（promote / reject 走这里）；MVP 不暴露 promote 给 LLM

参考 ``backtest_runs.py`` 的事务约定。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection


def compute_code_hash(code: str) -> str:
    """sha256(code) 前 16 hex 用于 UNIQUE 去重。"""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


async def insert_candidate(
    conn: AsyncConnection,
    *,
    code: str,
    description: str = "",
    author: str = "llm",
    author_id: UUID | None = None,
    owner_account_id: UUID | None = None,
    audit: dict[str, Any] | None = None,
) -> tuple[UUID, bool]:
    """落一行候选。

    Returns:
        ``(candidate_id, created)``；``created=False`` 表示已存在同 hash，
        返回的是老 ID（幂等）。

    幂等理由：LLM 经常重复写相同策略；调用方应当作"获取或新建"语义用。
    """
    code_hash = compute_code_hash(code)
    audit_json = json.dumps(audit, default=str) if audit is not None else None

    # 先查
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM strategy_candidates WHERE code_hash = %s",
            (code_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            return row["id"], False

    # 不存在 → 写
    candidate_id = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO strategy_candidates (
                id, code, code_hash, description, author, author_id,
                owner_account_id, audit
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(candidate_id),
                code,
                code_hash,
                description,
                author,
                str(author_id) if author_id else None,
                str(owner_account_id) if owner_account_id else None,
                audit_json,
            ),
        )
    return candidate_id, True


async def get_candidate(
    conn: AsyncConnection,
    candidate_id: UUID,
) -> dict[str, Any] | None:
    """按 id 取候选完整行；不存在返 None。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, code, code_hash, description, author, author_id,
                   owner_account_id, status, metrics, fitness, last_backtest_run_id,
                   audit, created_at, updated_at
            FROM strategy_candidates
            WHERE id = %s
            """,
            (str(candidate_id),),
        )
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_candidates(
    conn: AsyncConnection,
    *,
    status: str | None = None,
    author_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列候选（按 fitness DESC, created_at DESC，未跑过回测的排最后）。

    Args:
        status: 可选过滤 'candidate' / 'rejected' / 'promoted'
        author_id: 可选只看某用户创建的
    """
    sql = (
        "SELECT id, code, code_hash, description, author, author_id, "
        "owner_account_id, status, metrics, fitness, last_backtest_run_id, "
        "audit, created_at, updated_at "
        "FROM strategy_candidates WHERE 1=1"
    )
    params: list[Any] = []
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    if author_id is not None:
        sql += " AND author_id = %s"
        params.append(str(author_id))
    sql += " ORDER BY fitness DESC NULLS LAST, created_at DESC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_after_backtest(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    metrics: dict[str, Any],
    fitness: float,
    backtest_run_id: UUID | None,
) -> None:
    """回测跑完后回写 metrics / fitness / last_backtest_run_id。

    每次 backtest 都覆盖（MVP 不保留"历次回测 in candidate"，要看历次去
    backtest_runs 表查）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET metrics = %s,
                fitness = %s,
                last_backtest_run_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                json.dumps(metrics, default=str),
                fitness,
                str(backtest_run_id) if backtest_run_id else None,
                str(candidate_id),
            ),
        )


async def set_status(
    conn: AsyncConnection,
    candidate_id: UUID,
    status: str,
) -> None:
    """改 status。promote / reject 走这里；CHECK 约束保证只能是合法值。"""
    if status not in ("candidate", "rejected", "promoted"):
        raise ValueError(f"invalid status {status!r}")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (status, str(candidate_id)),
        )


async def promote_candidate(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    reason: str,
    promoted_by: str,
) -> None:
    """把 ``status='candidate'`` 的行切到 ``'promoted'`` 并把 promote 元数据并进
    ``audit.promotion`` JSONB 字段（reason / promoted_by / promoted_at ISO UTC 字符串）。

    端点层负责"当前 status==candidate + fitness 非空"校验——本函数不再二次 guard
    （避免读写分裂导致的 race）。一条 UPDATE 同时改 status + audit + updated_at 保证原子性。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE strategy_candidates
            SET status = 'promoted',
                audit = COALESCE(audit, '{}'::jsonb) || jsonb_build_object(
                    'promotion',
                    jsonb_build_object(
                        'reason', %s::text,
                        'promoted_by', %s::text,
                        'promoted_at', to_char(
                            NOW() AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                        )
                    )
                ),
                updated_at = NOW()
            WHERE id = %s
            """,
            (reason, promoted_by, str(candidate_id)),
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    """psycopg dict_row → 简化 dict。JSONB 已 decode；UUID 保留对象。"""
    return {
        "id": row["id"],
        "code": row["code"],
        "code_hash": row["code_hash"],
        "description": row["description"],
        "author": row["author"],
        "author_id": row["author_id"],
        "owner_account_id": row["owner_account_id"],
        "status": row["status"],
        "metrics": row["metrics"],
        "fitness": row["fitness"],
        "last_backtest_run_id": row["last_backtest_run_id"],
        "audit": row["audit"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

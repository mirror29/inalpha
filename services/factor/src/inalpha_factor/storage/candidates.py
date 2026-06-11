"""factor_candidates 表读写（D-12 · 因子发现 L1）。

行为约定（对齐 paper 的 strategy_candidates 模式）：

- ``insert_candidate`` 按 ``expression_hash``（sha256 前 16 hex）UNIQUE 幂等：
  撞同表达式返回已有行 id（LLM 重复 propose 不报错、不重复落）
- ``review`` 是唯一的状态迁移入口：pending_review → registered / rejected；
  **不暴露给任何 LLM tool**（register 门，比 modelInvocable=false 更硬）
- ``list_registered`` 给 custom adapter 注入 catalog 用
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

_COLUMNS = (
    "id, expression, expression_hash, name, hypothesis, proposed_by, "
    "venue, symbol, timeframe, test_results, batch_id, n_tested, status, "
    "reviewed_by, reviewed_at, review_note, created_at, updated_at"
)


def compute_expression_hash(expression: str) -> str:
    """sha256(expression) 前 16 hex；同时是 registered 因子的 id 后缀（custom.<hash>）。"""
    return hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]


async def insert_candidate(
    conn: AsyncConnection,
    *,
    expression: str,
    hypothesis: str,
    name: str | None = None,
    proposed_by: str = "agent",
    venue: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    test_results: dict[str, Any] | None = None,
    batch_id: UUID | None = None,
    n_tested: int = 1,
) -> tuple[UUID, bool]:
    """落一行候选；同 expression_hash 已存在 → 返老行 id（created=False，幂等）。"""
    expr_hash = compute_expression_hash(expression)
    candidate_id = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO factor_candidates (
                id, expression, expression_hash, name, hypothesis, proposed_by,
                venue, symbol, timeframe, test_results, batch_id, n_tested
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (expression_hash) DO NOTHING
            RETURNING id
            """,
            (
                str(candidate_id), expression, expr_hash, name, hypothesis,
                proposed_by, venue, symbol, timeframe,
                json.dumps(test_results or {}, default=str),
                str(batch_id) if batch_id else None, n_tested,
            ),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return inserted["id"], True
        await cur.execute(
            "SELECT id FROM factor_candidates WHERE expression_hash = %s",
            (expr_hash,),
        )
        row = await cur.fetchone()
        if row is not None:
            return row["id"], False
    return candidate_id, True  # 理论不可达（DO NOTHING 未插却查不到）


async def get_candidate(conn: AsyncConnection, candidate_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT {_COLUMNS} FROM factor_candidates WHERE id = %s",
            (str(candidate_id),),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def list_candidates(
    conn: AsyncConnection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = f"SELECT {_COLUMNS} FROM factor_candidates WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def review(
    conn: AsyncConnection,
    candidate_id: UUID,
    *,
    action: str,  # "register" | "reject"
    reviewed_by: str,
    note: str | None = None,
) -> dict[str, Any] | None:
    """人工审核：pending_review → registered / rejected。

    只迁移当前 pending_review 的行（重复审核 / 状态错位返 None，调用方转 409）。
    """
    new_status = "registered" if action == "register" else "rejected"
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            UPDATE factor_candidates
            SET status = %s, reviewed_by = %s, reviewed_at = NOW(),
                review_note = %s, updated_at = NOW()
            WHERE id = %s AND status = 'pending_review'
            RETURNING {_COLUMNS}
            """,
            (new_status, reviewed_by, note, str(candidate_id)),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def list_registered(conn: AsyncConnection) -> list[dict[str, Any]]:
    """全部已注册候选（custom adapter 注入 catalog 用）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, expression, expression_hash, name, hypothesis "
            "FROM factor_candidates WHERE status = 'registered' ORDER BY updated_at"
        )
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]

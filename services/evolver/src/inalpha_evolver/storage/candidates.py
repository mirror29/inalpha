"""strategy_evo_candidates 的 slot 持久化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from ..loop_fencing import fenced_baseline_write

_COLUMNS = """candidate_id,run_id,slot,generation,parent_id,stage,outcome,source_code,
source_hash,unified_diff,mutation_hint,llm_cost_usd,cache_hit_tokens,input_tokens,
output_tokens,fitness,evaluation_snapshot,audit_snapshot,contract_snapshot,error_code,
error_message,overfitting_risk,data_epoch,created_at,updated_at"""


@fenced_baseline_write
async def insert_slot(
    conn: AsyncConnection,
    run_id: UUID,
    slot: int,
    mutation_hint: str,
) -> dict[str, Any]:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO strategy_evo_candidates(candidate_id,run_id,slot,generation,
mutation_hint,stage,outcome,status,report)VALUES(%s,%s,%s,1,%s,'mutation','pending',
'evaluated',NULL)ON CONFLICT(run_id,slot)DO UPDATE SET updated_at=NOW()
RETURNING {_COLUMNS}""",
            (uuid4(), run_id, slot, mutation_hint),
        )
        row = await cur.fetchone()
    return dict(row)


@fenced_baseline_write
async def update_slot(
    conn: AsyncConnection,
    run_id: UUID,
    slot: int,
    **values: Any,
) -> dict[str, Any] | None:
    values["updated_at"] = datetime.now(UTC)
    assignments = ",".join(f"{key}=%s" for key in values)
    params = [json.dumps(value) if isinstance(value, dict) else value for value in values.values()]
    params.extend([run_id, slot])
    async with conn.cursor() as cur:
        await cur.execute(
            f"UPDATE strategy_evo_candidates SET {assignments} WHERE run_id=%s AND slot=%s RETURNING {_COLUMNS}",
            params,
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def source_exists(
    conn: AsyncConnection,
    run_id: UUID,
    source_hash: str,
) -> bool:
    """判断 run 内是否已有相同非空候选源码。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM strategy_evo_candidates WHERE run_id=%s AND source_hash=%s LIMIT 1",
            (run_id, source_hash),
        )
        return await cur.fetchone() is not None


async def list_candidates(
    conn: AsyncConnection,
    run_id: UUID,
    owner_account_id: UUID,
) -> list[dict[str, Any]]:
    selected = ",".join(f"c.{name.strip()}" for name in _COLUMNS.split(","))
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {selected} FROM strategy_evo_candidates c JOIN strategy_evo_runs r
USING(run_id)WHERE c.run_id=%s AND r.owner_account_id=%s ORDER BY c.slot""",
            (run_id, owner_account_id),
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_candidate(
    conn: AsyncConnection,
    candidate_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    selected = ",".join(f"c.{name.strip()}" for name in _COLUMNS.split(","))
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {selected} FROM strategy_evo_candidates c JOIN strategy_evo_runs r
USING(run_id)WHERE c.candidate_id=%s AND r.owner_account_id=%s""",
            (candidate_id, owner_account_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


@fenced_baseline_write
async def close_pending(
    conn: AsyncConnection,
    run_id: UUID,
    *,
    error_code: str,
    error_message: str,
) -> int:
    """把 run 终态遗留的 pending slot 原子收口为 cancelled。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE strategy_evo_candidates SET stage='completed',outcome='cancelled',
error_code=%s,error_message=%s,updated_at=%s WHERE run_id=%s AND outcome='pending'""",
            (error_code, error_message[:1000], datetime.now(UTC), run_id),
        )
        return cur.rowcount


async def summarize(conn: AsyncConnection, run_id: UUID) -> dict[str, int | float]:
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT count(*) attempted,count(*)FILTER(WHERE outcome='succeeded') succeeded,
count(*)FILTER(WHERE outcome NOT IN('pending','succeeded')) rejected,
COALESCE(sum(llm_cost_usd),0) llm_cost_usd FROM strategy_evo_candidates WHERE run_id=%s""",
            (run_id,),
        )
        row = await cur.fetchone()
    return dict(row)

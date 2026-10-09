"""Owner-bound approval preparation costs; never infer a whole conversation's cost."""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def for_run(conn: AsyncConnection, run_id: UUID, owner: UUID) -> dict[str, Any]:
    async with conn.cursor() as cur:
        await cur.execute("""SELECT
 op.execution_input->'view'->>'chatInvocationId' invocation_id,
 (SELECT COUNT(*) FROM (
   SELECT operation_id::text FROM evolution_approval_operations other
   WHERE other.auth_sub=op.auth_sub
     AND other.execution_input->'view'->>'chatInvocationId'=op.execution_input->'view'->>'chatInvocationId'
   UNION SELECT operation_id::text FROM chat_evolution_operations other
   WHERE other.auth_sub=op.auth_sub
     AND other.invocation_id::text=op.execution_input->'view'->>'chatInvocationId'
 ) shared) shared_approval_count,
 usage.call_count,usage.known_cost_usd,usage.unknown_cost_count
FROM strategy_evo_runs r
JOIN evolution_approval_operations op
 ON op.operation_id::text=r.idempotency_key AND op.auth_sub=r.requested_by_sub
 AND op.tool_name='evolver.run_evolution'
LEFT JOIN LATERAL (
 SELECT COUNT(*) call_count,SUM(estimated_cost_usd) known_cost_usd,
 COUNT(*) FILTER(WHERE estimated_cost_usd IS NULL) unknown_cost_count
 FROM chat_usage_receipts receipt
 WHERE receipt.auth_sub=op.auth_sub AND receipt.call_source='chat'
 AND receipt.invocation_id::text=op.execution_input->'view'->>'chatInvocationId'
) usage ON TRUE
WHERE r.run_id=%s AND r.owner_account_id=%s""", (run_id, owner))
        row = await cur.fetchone()
    if not row or not row.get("invocation_id"):
        async with conn.cursor() as cur:
            await cur.execute("""SELECT loop.loop_id FROM evolution_loops loop
JOIN strategy_evo_runs run ON run.run_id=loop.e1_run_id AND run.owner_account_id=loop.owner_account_id
WHERE loop.e1_run_id=%s AND loop.owner_account_id=%s
 AND loop.operation_id=run.idempotency_key AND loop.requested_by_sub=run.requested_by_sub""", (run_id, owner))
            loop = await cur.fetchone()
        return await for_e2_task(conn, loop["loop_id"], owner, kind="loop") if loop else {"linked": False}
    return {
        "linked": True,
        "call_count": int(row["call_count"]),
        "known_cost_usd": float(row["known_cost_usd"]) if row["known_cost_usd"] is not None else None,
        "unknown_cost_count": int(row["unknown_cost_count"]),
        "shared_approval_count": int(row["shared_approval_count"]),
    }


async def for_e2_task(conn: AsyncConnection, task_id: UUID, owner: UUID, *, kind: str) -> dict[str, Any]:
    """Read the original E2 operation even when an active workflow was reused."""
    if kind == "loop":
        relation = """SELECT link.auth_sub,link.invocation_id FROM evolution_loops task
JOIN chat_evolution_operations link
 ON link.operation_id::text=task.operation_id AND link.auth_sub=task.requested_by_sub
WHERE task.loop_id=%s AND task.owner_account_id=%s"""
    elif kind == "campaign":
        relation = """SELECT link.auth_sub,link.invocation_id FROM evolution_campaigns task
LEFT JOIN evolution_loops loop ON loop.campaign_id=task.campaign_id AND loop.owner_account_id=task.owner_account_id
JOIN chat_evolution_operations link ON link.auth_sub=task.requested_by_sub
 AND (link.operation_id::text=task.idempotency_key OR link.operation_id::text=loop.operation_id)
WHERE task.campaign_id=%s AND task.owner_account_id=%s
ORDER BY link.created_at LIMIT 1"""
    else:
        raise ValueError("unsupported E2 task kind")
    async with conn.cursor() as cur:
        await cur.execute(f"""WITH association AS ({relation})
SELECT association.invocation_id,usage.call_count,usage.known_cost_usd,usage.unknown_cost_count,
 (SELECT COUNT(*) FROM (
   SELECT operation_id::text FROM chat_evolution_operations
   WHERE auth_sub=association.auth_sub AND invocation_id=association.invocation_id
   UNION
   SELECT operation_id::text FROM evolution_approval_operations
   WHERE auth_sub=association.auth_sub
     AND execution_input->'view'->>'chatInvocationId'=association.invocation_id::text
 ) shared) shared_approval_count
FROM association LEFT JOIN LATERAL (
 SELECT COUNT(*) call_count,SUM(estimated_cost_usd) known_cost_usd,
 COUNT(*) FILTER(WHERE estimated_cost_usd IS NULL) unknown_cost_count
 FROM chat_usage_receipts receipt WHERE receipt.auth_sub=association.auth_sub
 AND receipt.invocation_id=association.invocation_id AND receipt.call_source='chat'
) usage ON TRUE""", (task_id, owner))
        row = await cur.fetchone()
    if not row:
        return {"linked": False}
    return {
        "linked": True, "call_count": int(row["call_count"]),
        "known_cost_usd": float(row["known_cost_usd"]) if row["known_cost_usd"] is not None else None,
        "unknown_cost_count": int(row["unknown_cost_count"]),
        "shared_approval_count": int(row["shared_approval_count"]),
    }

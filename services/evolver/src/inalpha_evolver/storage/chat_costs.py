"""Owner-bound approval preparation costs; never infer a whole conversation's cost."""
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def for_run(conn: AsyncConnection, run_id: UUID, owner: UUID) -> dict[str, Any]:
    async with conn.cursor() as cur:
        await cur.execute("""SELECT
 op.execution_input->'view'->>'chatInvocationId' invocation_id,
 (SELECT COUNT(*) FROM evolution_approval_operations other
  WHERE other.auth_sub=op.auth_sub
    AND other.execution_input->'view'->>'chatInvocationId'=op.execution_input->'view'->>'chatInvocationId') shared_approval_count,
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
        return {"linked": False}
    return {
        "linked": True,
        "call_count": int(row["call_count"]),
        "known_cost_usd": float(row["known_cost_usd"]) if row["known_cost_usd"] is not None else None,
        "unknown_cost_count": int(row["unknown_cost_count"]),
        "shared_approval_count": int(row["shared_approval_count"]),
    }

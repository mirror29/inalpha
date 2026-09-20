"""Durable ownership for E1-to-E2 handoffs; no external calls inside transactions."""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from inalpha_shared.errors import ConflictError
from psycopg import AsyncConnection


async def claim_next(
    conn: AsyncConnection,
    *,
    worker_id: str,
    ttl_s: int = 60,
) -> dict[str, Any] | None:
    """Claim one pre-campaign loop without blocking another dispatcher."""
    if not worker_id or not 1 <= ttl_s <= 600:
        raise ValueError("worker identity and a 1-600 second lease are required")
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """WITH picked AS (
  SELECT loop_id FROM evolution_loops
  WHERE campaign_id IS NULL AND e1_run_id IS NOT NULL
    AND status IN ('target_resolved','baseline_ready')
    AND next_attempt_at<=clock_timestamp()
    AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp())
  ORDER BY next_attempt_at,updated_at,loop_id
  FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE evolution_loops l SET lease_owner=%s,lease_token=%s,
lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second'
FROM picked WHERE l.loop_id=picked.loop_id RETURNING l.*""",
                (worker_id, uuid4(), ttl_s),
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def renew(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    ttl_s: int = 60,
) -> bool:
    """Keep only the current owner's live lease; an expired lease cannot be revived."""
    if not 1 <= ttl_s <= 600:
        raise ValueError("lease duration must be between 1 and 600 seconds")
    result = await conn.execute(
        """UPDATE evolution_loops SET lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second'
WHERE loop_id=%s AND owner_account_id=%s AND lease_token=%s
AND lease_expires_at>=clock_timestamp()""",
        (ttl_s, loop_id, owner_account_id, lease_token),
    )
    return result.rowcount == 1


async def defer(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    delay_s: int = 30,
) -> bool:
    """Release a valid lease with durable backoff; expired workers cannot change it."""
    if not 0 <= delay_s <= 3600:
        raise ValueError("retry delay must be between 0 and 3600 seconds")
    result = await conn.execute(
        """UPDATE evolution_loops SET lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
next_attempt_at=clock_timestamp()+%s*INTERVAL '1 second'
WHERE loop_id=%s AND owner_account_id=%s AND lease_token=%s
AND lease_expires_at>=clock_timestamp()""",
        (delay_s, loop_id, owner_account_id, lease_token),
    )
    return result.rowcount == 1


async def complete_step(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    step_key: Literal["baseline", "campaign"],
    output_id: UUID,
) -> bool:
    """Commit one immutable handoff and its UI event atomically under a current lease."""
    if step_key not in {"baseline", "campaign"}:
        raise ValueError("unsupported evolution loop step")
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT * FROM evolution_loops WHERE loop_id=%s AND owner_account_id=%s
AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND status IN ('target_resolved','baseline_ready')
FOR UPDATE""",
                (loop_id, owner_account_id, lease_token),
            )
            loop = await cur.fetchone()
            if loop is None:
                return False
            await cur.execute(
                "SELECT output_id FROM evolution_loop_steps WHERE loop_id=%s AND step_key=%s",
                (loop_id, step_key),
            )
            previous = await cur.fetchone()
            if previous is not None:
                if previous["output_id"] != output_id:
                    raise ConflictError("step output is immutable", code="LOOP_STEP_CONFLICT")
                return True
            if step_key == "baseline":
                await cur.execute(
                    """SELECT run_id FROM strategy_evo_runs WHERE run_id=%s
AND run_id=%s AND owner_account_id=%s AND status='completed'""",
                    (output_id, loop["e1_run_id"], owner_account_id),
                )
                next_status = "baseline_ready"
                if loop["campaign_id"] is not None:
                    raise ConflictError("baseline already handed off", code="LOOP_STEP_CONFLICT")
            else:
                await cur.execute(
                    """SELECT campaign_id FROM evolution_campaigns WHERE campaign_id=%s
AND owner_account_id=%s AND source_run_id=%s
AND EXISTS(SELECT 1 FROM evolution_loop_steps WHERE loop_id=%s AND step_key='baseline')""",
                    (output_id, owner_account_id, loop["e1_run_id"], loop_id),
                )
                next_status = "baseline_ready"
                if loop["campaign_id"] not in (None, output_id):
                    raise ConflictError("campaign already handed off", code="LOOP_STEP_CONFLICT")
            if await cur.fetchone() is None:
                raise ConflictError("step output is not ready or owned", code="LOOP_STEP_NOT_READY")
            await cur.execute(
                """UPDATE evolution_loops SET status=%s,
campaign_id=CASE WHEN %s='campaign' THEN %s ELSE campaign_id END,
state_version=state_version+1,updated_at=clock_timestamp()
WHERE loop_id=%s AND owner_account_id=%s AND lease_token=%s
AND lease_expires_at>=clock_timestamp() RETURNING state_version""",
                (next_status, step_key, output_id, loop_id, owner_account_id, lease_token),
            )
            updated = await cur.fetchone()
            if updated is None:
                return False
            await cur.execute(
                "INSERT INTO evolution_loop_steps(loop_id,step_key,output_id) VALUES(%s,%s,%s)",
                (loop_id, step_key, output_id),
            )
            await cur.execute(
                """INSERT INTO evolution_loop_events(loop_id,version,event_type,payload)
VALUES(%s,%s,'step_completed',%s::jsonb)""",
                (
                    loop_id,
                    updated["state_version"],
                    json.dumps({"step": step_key, "output_id": str(output_id)}),
                ),
            )
            return True

"""Durable owner-scoped EvolutionLoop projections and versioned events."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from inalpha_shared.errors import ConflictError, NotFoundError
from psycopg import AsyncConnection

from . import loop_dispatch

_COLUMNS = """loop_id,owner_account_id,requested_by_sub,operation_id,target_kind,target_id,
target_snapshot,status,e1_run_id,campaign_id,forward_sandbox_id,holdout_attempt_id,
frozen_config,budget,failure_code,failure_message,state_version,created_at,updated_at,finished_at"""
_ACTIVE = (
    "target_resolved",
    "baseline_ready",
    "campaign_running",
    "candidate_locked",
    "waiting_forward",
    "holdout_running",
)
_CAMPAIGN_STATUS = {
    "draft": "baseline_ready",
    "replaying": "campaign_running",
    "candidate_locked": "candidate_locked",
    "waiting_forward": "waiting_forward",
    "holdout_ready": "holdout_running",
    "graduated": "adoption_ready",
    "rejected": "rejected",
    "insufficient_evidence": "insufficient_evidence",
    "failed": "failed",
    "aborted": "failed",
}


async def ensure_for_e1_run(
    conn: AsyncConnection,
    *,
    owner_account_id: UUID,
    requested_by_sub: str,
    operation_id: str,
    target_kind: str,
    target_id: str,
    target_snapshot: dict[str, Any],
    e1_run_id: UUID,
    frozen_config: dict[str, Any],
    budget: dict[str, Any],
) -> dict[str, Any]:
    """Persist the workflow in the same transaction as its initial E1 run."""
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{owner_account_id}:{target_kind}:{target_id}",),
            )
            await cur.execute(
                "SELECT run_id FROM strategy_evo_runs WHERE run_id=%s AND owner_account_id=%s",
                (e1_run_id, owner_account_id),
            )
            if await cur.fetchone() is None:
                raise NotFoundError("source evolution run not found", code="SOURCE_RUN_NOT_FOUND")
            await cur.execute(
                f"SELECT {_COLUMNS} FROM evolution_loops WHERE owner_account_id=%s AND operation_id=%s",
                (owner_account_id, operation_id),
            )
            existing = await cur.fetchone()
            if existing is not None:
                if (
                    existing["target_kind"] != target_kind
                    or existing["target_id"] != target_id
                    or existing["e1_run_id"] != e1_run_id
                    or existing["frozen_config"] != frozen_config
                    or existing["budget"] != budget
                ):
                    raise ConflictError("loop operation reused", code="IDEMPOTENCY_KEY_REUSED")
                return dict(existing)
            active = await get_active_loop_for_target(
                conn,
                owner_account_id,
                target_kind,
                target_id,
            )
            if active is not None:
                return active
            await cur.execute(
                f"""INSERT INTO evolution_loops(
loop_id,owner_account_id,requested_by_sub,operation_id,target_kind,target_id,target_snapshot,
status,e1_run_id,frozen_config,budget)
VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,'target_resolved',%s,%s::jsonb,%s::jsonb)
ON CONFLICT(owner_account_id,operation_id) DO NOTHING RETURNING {_COLUMNS}""",
                (
                    uuid4(),
                    owner_account_id,
                    requested_by_sub,
                    operation_id,
                    target_kind,
                    target_id,
                    json.dumps(target_snapshot),
                    e1_run_id,
                    json.dumps(frozen_config),
                    json.dumps(budget),
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise ConflictError("loop operation reused", code="IDEMPOTENCY_KEY_REUSED")
            await cur.execute(
                """INSERT INTO evolution_loop_events(loop_id,version,event_type,payload)
VALUES(%s,0,'loop_created',%s::jsonb)""",
                (row["loop_id"], json.dumps({"e1_run_id": str(e1_run_id)})),
            )
            return dict(row)


async def ensure_for_campaign(
    conn: AsyncConnection,
    *,
    owner_account_id: UUID,
    requested_by_sub: str,
    operation_id: str,
    target_kind: str,
    target_id: str,
    target_snapshot: dict[str, Any],
    e1_run_id: UUID | None,
    campaign_id: UUID,
    frozen_config: dict[str, Any],
    budget: dict[str, Any],
    lease_token: UUID | None = None,
) -> dict[str, Any]:
    """Create once or reuse the active loop for repeated target clicks/messages."""
    lock_key = f"{owner_account_id}:{target_kind}:{target_id}"
    async with conn.cursor() as cur:
        await cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,))
        await cur.execute(
            f"""SELECT {_COLUMNS} FROM evolution_loops
WHERE owner_account_id=%s AND target_kind=%s AND target_id=%s AND status=ANY(%s)
ORDER BY created_at DESC LIMIT 1""",
            (owner_account_id, target_kind, target_id, list(_ACTIVE)),
        )
        existing = await cur.fetchone()
        if existing is not None:
            if existing["campaign_id"] is None:
                if existing["e1_run_id"] != e1_run_id:
                    raise ConflictError("loop baseline mismatch", code="LOOP_BASELINE_MISMATCH")
                if lease_token is None or not await loop_dispatch.complete_step(
                    conn,
                    loop_id=existing["loop_id"],
                    owner_account_id=owner_account_id,
                    lease_token=lease_token,
                    step_key="campaign",
                    output_id=campaign_id,
                ):
                    raise ConflictError("loop lease lost", code="LOOP_LEASE_LOST")
                linked = await get_loop(conn, existing["loop_id"], owner_account_id)
                assert linked is not None
                return linked
            if existing["campaign_id"] != campaign_id:
                raise ConflictError(
                    "loop already has a campaign",
                    code="LOOP_CAMPAIGN_ALREADY_LINKED",
                )
            return dict(existing)
        loop_id = uuid4()
        await cur.execute(
            f"""INSERT INTO evolution_loops(
loop_id,owner_account_id,requested_by_sub,operation_id,target_kind,target_id,target_snapshot,
status,e1_run_id,campaign_id,frozen_config,budget)
VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,'baseline_ready',%s,%s,%s::jsonb,%s::jsonb)
ON CONFLICT(owner_account_id,operation_id) DO UPDATE SET operation_id=EXCLUDED.operation_id
RETURNING {_COLUMNS}""",
            (
                loop_id,
                owner_account_id,
                requested_by_sub,
                operation_id,
                target_kind,
                target_id,
                json.dumps(target_snapshot),
                e1_run_id,
                campaign_id,
                json.dumps(frozen_config),
                json.dumps(budget),
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        await cur.execute(
            """INSERT INTO evolution_loop_events(loop_id,version,event_type,payload)
VALUES(%s,0,'loop_created',%s::jsonb) ON CONFLICT DO NOTHING""",
            (row["loop_id"], json.dumps({"campaign_id": str(campaign_id)})),
        )
    return dict(row)


async def get_active_loop_for_target(
    conn: AsyncConnection,
    owner_account_id: UUID,
    target_kind: str,
    target_id: str,
) -> dict[str, Any] | None:
    """Resolve repeated commands to the single active workflow before creating a campaign."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_COLUMNS} FROM evolution_loops
WHERE owner_account_id=%s AND target_kind=%s AND target_id=%s AND status=ANY(%s)
ORDER BY created_at DESC LIMIT 1""",
            (owner_account_id, target_kind, target_id, list(_ACTIVE)),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    synced = await sync_from_campaign(conn, row["loop_id"], owner_account_id)
    return synced if synced is not None and synced["status"] in _ACTIVE else None


async def get_loop_by_campaign(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Load the compact workflow projection associated with one owner campaign."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT loop_id FROM evolution_loops
WHERE campaign_id=%s AND owner_account_id=%s""",
            (campaign_id, owner_account_id),
        )
        row = await cur.fetchone()
    return None if row is None else await get_loop(conn, row["loop_id"], owner_account_id)


async def get_loop_for_target(
    conn: AsyncConnection,
    owner_account_id: UUID,
    target_kind: str,
    target_id: UUID,
) -> dict[str, Any] | None:
    """Reuse active original targets; baseline detail always points to its existing workflow."""
    if target_kind != "e1_run":
        return await get_active_loop_for_target(conn, owner_account_id, target_kind, str(target_id))
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT loop_id FROM evolution_loops
WHERE owner_account_id=%s AND e1_run_id=%s ORDER BY created_at DESC,loop_id DESC LIMIT 1""",
            (owner_account_id, target_id),
        )
        row = await cur.fetchone()
    return None if row is None else await get_loop(conn, row["loop_id"], owner_account_id)


async def sync_from_campaign(
    conn: AsyncConnection,
    loop_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Project E1/campaign progress only after verifying the loop owner's scope."""
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                f"""SELECT l.{",l.".join(item.strip() for item in _COLUMNS.split(","))},
c.status AS campaign_status,c.forward_sandbox_id AS campaign_forward_sandbox_id,
c.failure_code AS campaign_failure_code,
c.failure_message AS campaign_failure_message,c.finished_at AS campaign_finished_at,
h.attempt_id AS current_holdout_attempt_id,
r.status AS e1_status,r.failure_code AS e1_failure_code,
r.failure_message AS e1_failure_message,r.finished_at AS e1_finished_at
FROM evolution_loops l
LEFT JOIN evolution_campaigns c ON c.campaign_id=l.campaign_id AND c.owner_account_id=l.owner_account_id
LEFT JOIN strategy_evo_runs r ON r.run_id=l.e1_run_id AND r.owner_account_id=l.owner_account_id
LEFT JOIN evolution_holdout_attempts h ON h.campaign_id=c.campaign_id
WHERE l.loop_id=%s AND l.owner_account_id=%s FOR UPDATE OF l""",
                (loop_id, owner_account_id),
            )
            joined = await cur.fetchone()
            if joined is None:
                return None
            if joined["campaign_id"] is None and joined["status"] not in _ACTIVE:
                return {key.strip(): joined[key.strip()] for key in _COLUMNS.split(",")}
            if joined["campaign_id"] is None:
                next_status = {
                    "completed": "baseline_ready",
                    "failed": "failed",
                    "aborted": "failed",
                }.get(joined["e1_status"], "target_resolved")
                joined["campaign_failure_code"] = joined["e1_failure_code"]
                joined["campaign_failure_message"] = joined["e1_failure_message"]
                joined["campaign_finished_at"] = joined["e1_finished_at"]
            else:
                next_status = _CAMPAIGN_STATUS[str(joined["campaign_status"])]
            changed = (
                joined["status"] != next_status
                or joined["forward_sandbox_id"] != joined["campaign_forward_sandbox_id"]
                or joined["holdout_attempt_id"] != joined["current_holdout_attempt_id"]
                or joined["failure_code"] != joined["campaign_failure_code"]
                or joined["failure_message"] != joined["campaign_failure_message"]
            )
            if not changed:
                return {key.strip(): joined[key.strip()] for key in _COLUMNS.split(",")}
            await cur.execute(
                f"""UPDATE evolution_loops SET status=%s,forward_sandbox_id=%s,
holdout_attempt_id=%s,failure_code=%s,failure_message=%s,
finished_at=%s,state_version=state_version+1,updated_at=NOW()
WHERE loop_id=%s RETURNING {_COLUMNS}""",
                (
                    next_status,
                    joined["campaign_forward_sandbox_id"],
                    joined["current_holdout_attempt_id"],
                    joined["campaign_failure_code"],
                    joined["campaign_failure_message"],
                    joined["campaign_finished_at"] if next_status not in _ACTIVE else None,
                    loop_id,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            await cur.execute(
                """INSERT INTO evolution_loop_events(loop_id,version,event_type,payload)
VALUES(%s,%s,'state_changed',%s::jsonb)""",
                (
                    loop_id,
                    row["state_version"],
                    json.dumps({"status": next_status}),
                ),
            )
    return dict(row)


async def get_loop(
    conn: AsyncConnection,
    loop_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Load one owner-scoped loop after synchronizing its durable dependencies."""
    return await sync_from_campaign(conn, loop_id, owner_account_id)


async def list_loops(
    conn: AsyncConnection,
    owner_account_id: UUID,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """List compact loops and lazily synchronize the bounded result set."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT loop_id FROM evolution_loops WHERE owner_account_id=%s
ORDER BY updated_at DESC,loop_id DESC LIMIT %s""",
            (owner_account_id, limit),
        )
        ids = [row["loop_id"] for row in await cur.fetchall()]
    rows = [await get_loop(conn, loop_id, owner_account_id) for loop_id in ids]
    return [row for row in rows if row is not None]


async def list_events(
    conn: AsyncConnection,
    loop_id: UUID,
    owner_account_id: UUID,
    *,
    after_version: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return versioned increments without exposing logs, source or raw event text."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT e.loop_id,e.version,e.event_type,e.payload,e.created_at
FROM evolution_loop_events e JOIN evolution_loops l USING(loop_id)
WHERE e.loop_id=%s AND l.owner_account_id=%s AND e.version>%s
ORDER BY e.version LIMIT %s""",
            (loop_id, owner_account_id, after_version, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


__all__ = [
    "ensure_for_campaign",
    "ensure_for_e1_run",
    "get_active_loop_for_target",
    "get_loop",
    "get_loop_by_campaign",
    "list_events",
    "list_loops",
    "sync_from_campaign",
]

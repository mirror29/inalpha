"""run 列表、claim 与重启收口 SQL。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from .runs import _COLUMNS

_RUN_COLUMNS = ",".join(f"r.{name.strip()}" for name in _COLUMNS.split(","))
_SUMMARY_COLUMNS = """COALESCE(s.attempted,0) attempted,COALESCE(s.succeeded,0) succeeded,
COALESCE(s.rejected,0) rejected"""


async def list_runs(
    conn: AsyncConnection,
    owner_account_id: UUID,
    *,
    limit: int,
    cursor: tuple[datetime, UUID] | None = None,
) -> list[dict[str, Any]]:
    sql = f"""SELECT {_RUN_COLUMNS},{_SUMMARY_COLUMNS} FROM strategy_evo_runs r
LEFT JOIN LATERAL(SELECT count(*) attempted,
count(*)FILTER(WHERE outcome='succeeded') succeeded,
count(*)FILTER(WHERE outcome NOT IN('pending','succeeded')) rejected
FROM strategy_evo_candidates WHERE run_id=r.run_id)s ON TRUE
WHERE r.owner_account_id=%s"""
    params: list[Any] = [owner_account_id]
    if cursor is not None:
        sql += " AND (r.queued_at,r.run_id)<(%s,%s)"
        params.extend(cursor)
    sql += " ORDER BY r.queued_at DESC,r.run_id DESC LIMIT %s"
    params.append(limit)
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def claim_next(
    conn: AsyncConnection,
    *,
    queue_timeout_s: int = 86400,
) -> dict[str, Any] | None:
    """先收口过期队列项，再锁定最早 queued run 并原子切换为 running。"""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE strategy_evo_runs SET status='aborted',active_stage='aborted',
finished_at=%s,updated_at=%s,failure_code='EVOLUTION_QUEUE_TIMEOUT',
failure_message='run exceeded its queue deadline'
WHERE status='queued' AND queued_at < %s - make_interval(secs => %s)
AND NOT EXISTS(SELECT 1 FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=strategy_evo_runs.run_id)""",
            (now, now, now, queue_timeout_s),
        )
        await cur.execute(
            f"""WITH picked AS(SELECT run_id FROM strategy_evo_runs WHERE status='queued'
AND llm_snapshot_required AND llm_credential_grant_required
AND NOT EXISTS(SELECT 1 FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=strategy_evo_runs.run_id)
ORDER BY queued_at FOR UPDATE SKIP LOCKED LIMIT 1)UPDATE strategy_evo_runs r SET
status='running',active_stage='loading_data',started_at=%s,updated_at=%s FROM picked
WHERE r.run_id=picked.run_id RETURNING {",".join(f"r.{name.strip()}" for name in _COLUMNS.split(","))}""",
            (now, now),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def count_active(conn: AsyncConnection, owner_account_id: UUID) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) n FROM strategy_evo_runs WHERE owner_account_id=%s AND status=ANY(%s)",
            (owner_account_id, ["queued", "running", "cancelling"]),
        )
        row = await cur.fetchone()
    return int(row["n"])


async def abort_owned(
    conn: AsyncConnection,
    run_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE strategy_evo_runs SET status=CASE WHEN status='queued' THEN 'aborted'
ELSE 'cancelling' END,finished_at=CASE WHEN status='queued' THEN %s ELSE finished_at END,
updated_at=%s WHERE run_id=%s AND owner_account_id=%s AND status=ANY(%s) RETURNING {_COLUMNS}""",
            (now, now, run_id, owner_account_id, ["queued", "running"]),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def reconcile_interrupted(conn: AsyncConnection) -> int:
    """服务重启时保留 queued，收口可能已计费的 active run。"""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE strategy_evo_runs SET status='aborted',active_stage='aborted',
finished_at=%s,updated_at=%s,failure_code='EVOLUTION_UPGRADE_ABORTED',
failure_message='run queued by an image without owner LLM authorization metadata'
WHERE status='queued' AND (NOT llm_snapshot_required OR NOT llm_credential_grant_required)
AND NOT EXISTS(SELECT 1 FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=strategy_evo_runs.run_id)""",
            (now, now),
        )
        await cur.execute(
            """UPDATE strategy_evo_candidates c SET stage='completed',outcome='cancelled',
error_code='EVOLUTION_SERVICE_RESTARTED',error_message='service restarted during execution',
updated_at=%s FROM strategy_evo_runs r WHERE c.run_id=r.run_id AND c.outcome='pending'
AND r.status=ANY(%s)
AND NOT EXISTS(SELECT 1 FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=r.run_id)""",
            (now, ["running", "cancelling"]),
        )
        await cur.execute(
            """UPDATE strategy_evo_runs SET status='aborted',active_stage='aborted',
finished_at=%s,updated_at=%s,failure_code='EVOLUTION_SERVICE_RESTARTED',
failure_message='service restarted during execution'
WHERE status=ANY(%s)
AND NOT EXISTS(SELECT 1 FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=strategy_evo_runs.run_id)""",
            (now, now, ["running", "cancelling"]),
        )
        return cur.rowcount

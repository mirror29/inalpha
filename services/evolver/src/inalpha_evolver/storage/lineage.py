"""Compact owner-scoped experiment provenance without source code or credentials."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def for_run(conn: AsyncConnection, run_id: UUID, owner: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT run.run_id,run.seed_strategy_id,COALESCE(run.experiment_id,run.run_id) experiment_id,
CASE WHEN EXISTS(SELECT 1 FROM strategy_evo_runs origin
 WHERE origin.run_id=COALESCE(run.experiment_id,run.run_id) AND origin.owner_account_id=run.owner_account_id)
 THEN COALESCE(run.experiment_id,run.run_id) END root_run_id
FROM strategy_evo_runs run WHERE run.run_id=%s AND run.owner_account_id=%s""",
            (run_id, owner),
        )
        source = await cur.fetchone()
        if source is None:
            return None
        await cur.execute(
            """SELECT attempt.run_id,attempt.attempt_number,parent.run_id retry_of_run_id,attempt.status
FROM strategy_evo_runs attempt LEFT JOIN strategy_evo_runs parent
 ON parent.run_id=attempt.retry_of_run_id AND parent.owner_account_id=attempt.owner_account_id
WHERE attempt.owner_account_id=%s AND COALESCE(attempt.experiment_id,attempt.run_id)=%s
ORDER BY attempt.attempt_number,attempt.queued_at,attempt.run_id""",
            (owner, source["experiment_id"]),
        )
        attempts = [dict(row) for row in await cur.fetchall()]
        reference = source["seed_strategy_id"]
        if reference.startswith("evolution_candidate:"):
            try:
                source_candidate = UUID(reference.removeprefix("evolution_candidate:"))
            except ValueError:
                reference = None
            else:
                await cur.execute(
                    """SELECT 1 FROM strategy_evo_candidates candidate
JOIN strategy_evo_runs source_run USING(run_id)
WHERE candidate.candidate_id=%s AND source_run.owner_account_id=%s""",
                    (source_candidate, owner),
                )
                if await cur.fetchone() is None:
                    reference = None
        run_ids = [row["run_id"] for row in attempts]
        await cur.execute(
            """SELECT loop.loop_id,loop.status,loop.e1_run_id,c.campaign_id
FROM evolution_loops loop LEFT JOIN evolution_campaigns c ON c.campaign_id=loop.campaign_id AND c.owner_account_id=loop.owner_account_id
WHERE loop.owner_account_id=%s AND loop.e1_run_id=ANY(%s)
ORDER BY loop.created_at,loop.loop_id""",
            (owner, run_ids),
        )
        loops = [dict(row) for row in await cur.fetchall()]
        campaign_ids = [row["campaign_id"] for row in loops if row["campaign_id"]]
        campaigns = await _campaigns(cur, owner, run_ids, campaign_ids)
    return {
        "owner_account_id": owner,
        "source_reference": reference,
        "experiment_id": source["root_run_id"],
        "current_run_id": run_id,
        "attempts": attempts,
        "loops": loops,
        "campaigns": campaigns,
    }


async def _campaigns(
    cur, owner: UUID, run_ids: list[UUID], campaign_ids: list[UUID]
) -> list[dict[str, Any]]:
    await cur.execute(
        """SELECT c.campaign_id,source.run_id source_run_id,c.status,
 champion.implementation_id locked_candidate_id,
 forward.sandbox_id forward_sandbox_id,forward.status forward_status,
 forward.event_count forward_event_count,
 holdout.attempt_id holdout_attempt_id,holdout.status holdout_status,holdout.passed holdout_passed,
 (SELECT COUNT(*) FROM strategy_adoptions a
  WHERE a.campaign_id=c.campaign_id AND a.owner_account_id=c.owner_account_id) adoption_count
FROM evolution_campaigns c
LEFT JOIN strategy_evo_runs source ON source.run_id=c.source_run_id AND source.owner_account_id=c.owner_account_id
LEFT JOIN evolution_implementations champion
 ON champion.implementation_id=c.locked_candidate_id AND champion.campaign_id=c.campaign_id
LEFT JOIN paper_evolution_forward_sandboxes forward
 ON forward.sandbox_id=c.forward_sandbox_id AND forward.campaign_id=c.campaign_id
 AND forward.owner_account_id=c.owner_account_id AND forward.candidate_id=champion.implementation_id
LEFT JOIN evolution_holdout_attempts holdout
 ON holdout.campaign_id=c.campaign_id AND holdout.owner_account_id=c.owner_account_id
 AND holdout.candidate_id=champion.implementation_id
WHERE c.owner_account_id=%s AND (c.source_run_id=ANY(%s) OR c.campaign_id=ANY(%s))
ORDER BY c.created_at,c.campaign_id""",
        (owner, run_ids, campaign_ids),
    )
    return [dict(row) for row in await cur.fetchall()]


async def for_e2_task(
    conn: AsyncConnection, task_id: UUID, owner: UUID, *, kind: str
) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        if kind == "loop":
            await cur.execute(
                "SELECT e1_run_id,campaign_id FROM evolution_loops WHERE loop_id=%s AND owner_account_id=%s",
                (task_id, owner),
            )
        elif kind == "campaign":
            await cur.execute(
                "SELECT source_run_id e1_run_id,campaign_id FROM evolution_campaigns WHERE campaign_id=%s AND owner_account_id=%s",
                (task_id, owner),
            )
        else:
            raise ValueError("unsupported lineage task kind")
        task = await cur.fetchone()
    if task is None:
        return None
    if task["e1_run_id"]:
        result = await for_run(conn, task["e1_run_id"], owner)
        if result is not None:
            return result
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT loop.loop_id,loop.status,source.run_id e1_run_id,c.campaign_id FROM evolution_loops loop
LEFT JOIN strategy_evo_runs source ON source.run_id=loop.e1_run_id AND source.owner_account_id=loop.owner_account_id
LEFT JOIN evolution_campaigns c ON c.campaign_id=loop.campaign_id AND c.owner_account_id=loop.owner_account_id
WHERE loop.owner_account_id=%s AND (loop.loop_id=%s OR loop.campaign_id=%s) ORDER BY loop.created_at,loop.loop_id""",
            (owner, task_id, task["campaign_id"]),
        )
        loops = [dict(row) for row in await cur.fetchall()]
        campaigns = await _campaigns(
            cur, owner, [], [task["campaign_id"]] if task["campaign_id"] else []
        )
    return {
        "owner_account_id": owner,
        "source_reference": None,
        "experiment_id": None,
        "current_run_id": None,
        "attempts": [],
        "loops": loops,
        "campaigns": campaigns,
    }

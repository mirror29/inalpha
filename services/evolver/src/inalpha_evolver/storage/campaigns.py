"""Owner-scoped E2 campaign state, lease, holdout, and adoption storage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from ..hypothesis.compiler import canonical_spec_hash
from ..hypothesis.models import HypothesisSpec

_CAMPAIGN_COLUMNS = """campaign_id,owner_account_id,source_run_id,status,active_generation,
hypothesis_budget,implementations_per_hypothesis,max_generations,event_snapshot_id,data_snapshot_id,frozen_config,
llm_snapshot,llm_config_digest,llm_credential_grant,llm_cost_usd,
locked_candidate_id,holdout_consumed_at,forward_sandbox_id,forward_started_at,forward_deadline_at,
forward_event_count,forward_metrics,failure_code,failure_message,lease_owner,lease_token,
lease_expires_at,state_version,created_at,updated_at,finished_at"""
_HYPOTHESIS_COLUMNS = """hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,parent_ids,
spec,spec_hash,upper_credit,novelty_score,pareto_rank,selected,created_at"""
_IMPLEMENTATION_COLUMNS = """implementation_id,campaign_id,hypothesis_id,generation,profile,
source_code,source_hash,outcome,fitness,validation_metrics,event_metrics,evidence_quality,
novelty_score,fdr_pass,error_code,error_message,created_at,updated_at"""
_HOLDOUT_ATTEMPT_COLUMNS = """attempt_id,campaign_id,owner_account_id,candidate_id,status,
fencing_token,lease_expires_at,passed,evidence,started_at,finished_at,created_at,updated_at"""


async def insert_campaign(
    conn: AsyncConnection,
    *,
    owner_account_id: UUID,
    requested_by_sub: str,
    idempotency_key: str,
    request_hash: str,
    source_run_id: UUID | None,
    event_snapshot_id: UUID,
    frozen_config: dict[str, Any],
    llm_snapshot: dict[str, Any],
    llm_credential_grant: str | None,
    hypotheses: list[HypothesisSpec],
) -> dict[str, Any]:
    """Create one campaign and its generation-one hypotheses atomically."""
    campaign_id = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO evolution_campaigns(
campaign_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,
source_run_id,event_snapshot_id,frozen_config,
llm_snapshot,llm_config_digest,llm_credential_grant,
hypothesis_budget,implementations_per_hypothesis,max_generations)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,3,5)
ON CONFLICT(owner_account_id,idempotency_key) DO UPDATE
SET idempotency_key=EXCLUDED.idempotency_key RETURNING {_CAMPAIGN_COLUMNS},request_hash""",
            (
                campaign_id,
                owner_account_id,
                requested_by_sub,
                idempotency_key,
                request_hash,
                source_run_id,
                event_snapshot_id,
                json.dumps(frozen_config),
                json.dumps(llm_snapshot),
                llm_snapshot["config_digest"],
                llm_credential_grant,
                len(hypotheses),
            ),
        )
        campaign = await cur.fetchone()
        assert campaign is not None
        campaign_id = campaign["campaign_id"]
        for slot, spec in enumerate(hypotheses):
            await cur.execute(
                f"""INSERT INTO evolution_hypotheses(
hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,parent_ids,spec,spec_hash)
VALUES(%s,%s,1,%s,%s,%s,%s,%s::jsonb,%s)
ON CONFLICT(campaign_id,generation,slot) DO NOTHING
RETURNING {_HYPOTHESIS_COLUMNS}""",
                (
                    spec.hypothesis_id,
                    campaign_id,
                    slot,
                    spec.lineage_kind,
                    spec.lane,
                    spec.parent_ids,
                    spec.model_dump_json(),
                    canonical_spec_hash(spec),
                ),
            )
    return dict(campaign)


async def get_campaign(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    include_source: bool = True,
    include_implementations: bool = True,
) -> dict[str, Any] | None:
    """Load one owner-scoped campaign with generation-level projection."""
    implementation_columns = ",".join(
        column.strip() for column in _IMPLEMENTATION_COLUMNS.split(",")
        if include_source or column.strip() != "source_code"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_CAMPAIGN_COLUMNS} FROM evolution_campaigns
WHERE campaign_id=%s AND owner_account_id=%s""",
            (campaign_id, owner_account_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        campaign = dict(row)
        await cur.execute(
            """SELECT generation,count(*) AS hypothesis_count,
count(*) FILTER(WHERE selected) AS selected_count,
max(upper_credit) AS best_credit,max(novelty_score) AS best_novelty
FROM evolution_hypotheses WHERE campaign_id=%s GROUP BY generation ORDER BY generation""",
            (campaign_id,),
        )
        campaign["generations"] = [dict(item) for item in await cur.fetchall()]
        await cur.execute(
            f"""SELECT {_HYPOTHESIS_COLUMNS} FROM evolution_hypotheses
WHERE campaign_id=%s ORDER BY generation,slot""",
            (campaign_id,),
        )
        campaign["hypotheses"] = [dict(item) for item in await cur.fetchall()]
        if include_implementations:
            await cur.execute(
                f"""SELECT {implementation_columns} FROM evolution_implementations
WHERE campaign_id=%s ORDER BY generation,hypothesis_id,profile""",
                (campaign_id,),
            )
            campaign["implementations"] = [dict(item) for item in await cur.fetchall()]
        else:
            campaign["implementations"] = []
    return campaign


async def list_campaigns(
    conn: AsyncConnection,
    owner_account_id: UUID,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """List recent campaigns without loading candidate curves or evidence."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_CAMPAIGN_COLUMNS} FROM evolution_campaigns
WHERE owner_account_id=%s ORDER BY created_at DESC,campaign_id DESC LIMIT %s""",
            (owner_account_id, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


async def insert_implementation(
    conn: AsyncConnection,
    *,
    campaign_id: UUID,
    hypothesis_id: UUID,
    generation: int,
    profile: str,
    source_code: str,
    source_hash: str,
    lease_token: UUID,
) -> dict[str, Any]:
    """Insert one deterministic lower-level implementation idempotently."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO evolution_implementations(
implementation_id,campaign_id,hypothesis_id,generation,profile,source_code,source_hash)
SELECT %s,%s,%s,%s,%s,%s,%s FROM evolution_campaigns c
WHERE c.campaign_id=%s AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp()
ON CONFLICT(hypothesis_id,profile) DO UPDATE SET updated_at=NOW()
RETURNING {_IMPLEMENTATION_COLUMNS}""",
            (
                uuid4(),
                campaign_id,
                hypothesis_id,
                generation,
                profile,
                source_code,
                source_hash,
                campaign_id,
                lease_token,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("campaign lease fencing token was lost")
    return dict(row)


async def update_implementation(
    conn: AsyncConnection,
    implementation_id: UUID,
    *,
    campaign_id: UUID,
    lease_token: UUID,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist evaluation evidence without changing source identity."""
    updates = {**values, "updated_at": datetime.now(UTC)}
    assignments = ",".join(f"{key}=%s" for key in updates)
    params = [json.dumps(value) if isinstance(value, dict) else value for value in updates.values()]
    params.extend([implementation_id, campaign_id, campaign_id, lease_token])
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_implementations SET {assignments}
WHERE implementation_id=%s AND campaign_id=%s
AND EXISTS(SELECT 1 FROM evolution_campaigns c WHERE c.campaign_id=%s
 AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp())
RETURNING {_IMPLEMENTATION_COLUMNS}""",
            params,
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def find_cached_implementation(
    conn: AsyncConnection,
    campaign_id: UUID,
    source_hash: str,
) -> dict[str, Any] | None:
    """Reuse evaluation only within the same fully frozen campaign contract."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_IMPLEMENTATION_COLUMNS} FROM evolution_implementations
WHERE campaign_id=%s AND source_hash=%s AND outcome='succeeded'
ORDER BY updated_at DESC LIMIT 1""",
            (campaign_id, source_hash),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def list_generation_implementations(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
) -> list[dict[str, Any]]:
    """Return lightweight implementation evidence for selection."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_IMPLEMENTATION_COLUMNS} FROM evolution_implementations
WHERE campaign_id=%s AND generation=%s ORDER BY hypothesis_id,profile""",
            (campaign_id, generation),
        )
        return [dict(row) for row in await cur.fetchall()]


async def list_implementations_page(
    conn: AsyncConnection,
    campaign_id: UUID,
    *,
    owner_account_id: UUID,
    limit: int = 24,
    offset: int = 0,
    generation: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return bounded source-free implementation evidence for dashboard pagination."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = ["i.campaign_id=%s", "c.owner_account_id=%s"]
    params: list[Any] = [campaign_id, owner_account_id]
    if generation is not None:
        filters.append("i.generation=%s")
        params.append(generation)
    columns = ",".join(
        f"i.{column.strip()}" for column in _IMPLEMENTATION_COLUMNS.split(",")
        if column.strip() != "source_code"
    )
    params.extend([limit + 1, offset])
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {columns} FROM evolution_implementations i
JOIN evolution_campaigns c ON c.campaign_id=i.campaign_id
WHERE {' AND '.join(filters)}
ORDER BY i.generation DESC,i.updated_at DESC,i.implementation_id
LIMIT %s OFFSET %s""",
            params,
        )
        rows = [dict(row) for row in await cur.fetchall()]
    return rows[:limit], len(rows) > limit


async def update_hypothesis_scores(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    scores: list[dict[str, Any]],
    lease_token: UUID,
) -> None:
    """Persist upper credit, novelty, rank, and selection flags."""
    async with conn.cursor() as cur:
        for score in scores:
            await cur.execute(
                """UPDATE evolution_hypotheses SET upper_credit=%s,novelty_score=%s,
pareto_rank=%s,selected=%s WHERE campaign_id=%s AND generation=%s AND hypothesis_id=%s
AND EXISTS(SELECT 1 FROM evolution_campaigns c WHERE c.campaign_id=%s
 AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp())""",
                (
                    score["upper_credit"],
                    score["novelty_score"],
                    score["pareto_rank"],
                    score["selected"],
                    campaign_id,
                    generation,
                    score["hypothesis_id"],
                    campaign_id,
                    lease_token,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError("campaign lease fencing token was lost")


async def add_llm_cost(
    conn: AsyncConnection,
    campaign_id: UUID,
    amount_usd: float,
    lease_token: UUID,
) -> None:
    """Atomically append measured proposer cost without storing prompts or credentials."""
    if amount_usd < 0:
        raise ValueError("LLM cost cannot be negative")
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_campaigns SET llm_cost_usd=llm_cost_usd+%s,
state_version=state_version+1,updated_at=NOW() WHERE campaign_id=%s
AND lease_token=%s AND lease_expires_at>=clock_timestamp()""",
            (amount_usd, campaign_id, lease_token),
        )
        if cur.rowcount != 1:
            raise RuntimeError("campaign lease fencing token was lost")


async def replace_generation_hypotheses(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    hypotheses: list[HypothesisSpec],
    lease_token: UUID,
) -> None:
    """Replace an unevaluated scaffold with two-call Agent proposals atomically."""
    async with conn.cursor() as cur:
        await cur.execute(
            """DELETE FROM evolution_hypotheses h WHERE h.campaign_id=%s AND h.generation=%s
AND NOT EXISTS(SELECT 1 FROM evolution_implementations i
 WHERE i.campaign_id=h.campaign_id AND i.generation=h.generation)
AND EXISTS(SELECT 1 FROM evolution_campaigns c WHERE c.campaign_id=h.campaign_id
 AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp())""",
            (campaign_id, generation, lease_token),
        )
        if cur.rowcount == 0:
            await assert_active_lease(conn, campaign_id, lease_token)
    await insert_hypotheses(
        conn,
        campaign_id,
        generation,
        hypotheses,
        lease_token=lease_token,
    )


async def insert_hypotheses(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    hypotheses: list[HypothesisSpec],
    *,
    lease_token: UUID,
) -> None:
    """Append exactly one immutable next generation."""
    async with conn.cursor() as cur:
        for slot, spec in enumerate(hypotheses):
            await cur.execute(
                f"""INSERT INTO evolution_hypotheses(
hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,parent_ids,spec,spec_hash)
SELECT %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s FROM evolution_campaigns c
WHERE c.campaign_id=%s AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp()
ON CONFLICT(campaign_id,generation,slot) DO NOTHING
RETURNING {_HYPOTHESIS_COLUMNS}""",
                (
                    spec.hypothesis_id,
                    campaign_id,
                    generation,
                    slot,
                    spec.lineage_kind,
                    spec.lane,
                    spec.parent_ids,
                    spec.model_dump_json(),
                    canonical_spec_hash(spec),
                    campaign_id,
                    lease_token,
                ),
            )
            if cur.rowcount == 0:
                await assert_active_lease(conn, campaign_id, lease_token)


async def advance_generation(
    conn: AsyncConnection,
    campaign_id: UUID,
    *,
    current_generation: int,
    next_generation: int,
    lease_token: UUID,
) -> bool:
    """Advance only once after every implementation in the current generation is terminal."""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_campaigns c SET active_generation=%s,state_version=state_version+1,
updated_at=NOW() WHERE campaign_id=%s AND status='replaying' AND active_generation=%s
AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND NOT EXISTS(SELECT 1 FROM evolution_implementations i WHERE i.campaign_id=c.campaign_id
AND i.generation=%s AND i.outcome='pending')""",
            (
                next_generation,
                campaign_id,
                current_generation,
                lease_token,
                current_generation,
            ),
        )
        return cur.rowcount == 1


async def best_implementation(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
) -> dict[str, Any] | None:
    """Choose the unique FDR-passing champion with stable tie-breakers."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_IMPLEMENTATION_COLUMNS} FROM evolution_implementations
WHERE campaign_id=%s AND generation=%s AND outcome='succeeded' AND fdr_pass IS TRUE
ORDER BY fitness DESC,novelty_score DESC,implementation_id LIMIT 1""",
            (campaign_id, generation),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def transition(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    from_statuses: tuple[str, ...],
    to_status: str,
    values: dict[str, Any] | None = None,
    lease_token: UUID | None = None,
) -> dict[str, Any] | None:
    """Compare-and-swap campaign state and increment its projection version."""
    updates = dict(values or {})
    if to_status in {"graduated", "rejected", "insufficient_evidence", "failed", "aborted"}:
        updates["llm_credential_grant"] = None
    updates.update({"status": to_status, "updated_at": datetime.now(UTC)})
    assignments = ",".join(f"{key}=%s" for key in updates)
    params = [json.dumps(value) if isinstance(value, dict) else value for value in updates.values()]
    fence = ""
    if lease_token is not None:
        fence = " AND lease_token=%s AND lease_expires_at>=clock_timestamp()"
    params.extend([campaign_id, owner_account_id, list(from_statuses)])
    if lease_token is not None:
        params.append(lease_token)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET {assignments},state_version=state_version+1
WHERE campaign_id=%s AND owner_account_id=%s AND status=ANY(%s)
{fence}
RETURNING {_CAMPAIGN_COLUMNS}""",
            params,
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def acquire_lease(
    conn: AsyncConnection,
    campaign_id: UUID,
    *,
    worker_id: str,
    ttl_s: int = 60,
) -> dict[str, Any] | None:
    """Acquire or steal an expired campaign lease with a fresh fencing token."""
    token = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET lease_owner=%s,lease_token=%s,
lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
state_version=state_version+1,updated_at=clock_timestamp()
WHERE campaign_id=%s AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp() OR lease_owner=%s)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (worker_id, token, ttl_s, campaign_id, worker_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def claim_next_campaign(
    conn: AsyncConnection,
    *,
    worker_id: str,
    ttl_s: int = 60,
) -> dict[str, Any] | None:
    """Claim one active campaign step with SKIP LOCKED and a fresh fencing token."""
    token = uuid4()
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT campaign_id FROM evolution_campaigns
WHERE status IN ('replaying','candidate_locked','waiting_forward','holdout_ready')
AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp())
ORDER BY updated_at,campaign_id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            picked = await cur.fetchone()
            if picked is None:
                return None
            await cur.execute(
                f"""UPDATE evolution_campaigns SET lease_owner=%s,lease_token=%s,
lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
updated_at=clock_timestamp(),state_version=state_version+1
WHERE campaign_id=%s RETURNING {_CAMPAIGN_COLUMNS}""",
                (worker_id, token, ttl_s, picked["campaign_id"]),
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def renew_lease(
    conn: AsyncConnection,
    campaign_id: UUID,
    *,
    worker_id: str,
    lease_token: UUID,
    ttl_s: int = 60,
) -> bool:
    """Renew only the current fencing token; stale workers cannot extend ownership."""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_campaigns SET
lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',updated_at=clock_timestamp()
WHERE campaign_id=%s AND lease_owner=%s AND lease_token=%s AND lease_expires_at>=clock_timestamp()""",
            (
                ttl_s,
                campaign_id,
                worker_id,
                lease_token,
            ),
        )
        return cur.rowcount == 1


async def assert_active_lease(
    conn: AsyncConnection,
    campaign_id: UUID,
    lease_token: UUID,
) -> None:
    """Fail a worker step after its campaign fencing token expires or is replaced."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT 1 FROM evolution_campaigns
WHERE campaign_id=%s AND lease_token=%s AND lease_expires_at>=clock_timestamp()""",
            (campaign_id, lease_token),
        )
        if await cur.fetchone() is None:
            raise RuntimeError("campaign lease fencing token was lost")


async def lock_champion(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
) -> dict[str, Any] | None:
    """Irreversibly lock the deterministic best FDR candidate selected by the server."""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""WITH champion AS (
  SELECT implementation_id FROM evolution_implementations
  WHERE campaign_id=%s AND generation=(
    SELECT max_generations FROM evolution_campaigns WHERE campaign_id=%s
  ) AND outcome='succeeded' AND fdr_pass IS TRUE
  ORDER BY fitness DESC,novelty_score DESC,implementation_id LIMIT 1
)
UPDATE evolution_campaigns SET status='candidate_locked',
locked_candidate_id=(SELECT implementation_id FROM champion),
active_generation=max_generations,
llm_credential_grant=NULL,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='replaying'
AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND active_generation=max_generations AND locked_candidate_id IS NULL
AND EXISTS(SELECT 1 FROM champion)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                campaign_id,
                campaign_id,
                now,
                campaign_id,
                owner_account_id,
                lease_token,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def start_forward_sandbox(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    lease_token: UUID,
    sandbox_id: UUID,
) -> dict[str, Any] | None:
    """Advance only after Paper durably accepts the locked champion sandbox."""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET status='waiting_forward',forward_sandbox_id=%s,
forward_started_at=%s,forward_deadline_at=%s,state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='candidate_locked'
AND locked_candidate_id IS NOT NULL AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND EXISTS(SELECT 1 FROM paper_evolution_forward_sandboxes s
 WHERE s.sandbox_id=%s AND s.campaign_id=evolution_campaigns.campaign_id
 AND s.candidate_id=evolution_campaigns.locked_candidate_id)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                sandbox_id,
                now,
                now + timedelta(days=90),
                now,
                campaign_id,
                owner_account_id,
                lease_token,
                sandbox_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def record_forward(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    event_count: int,
    metrics: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist aggregate forward evidence and derive holdout readiness server-side."""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET
forward_event_count=GREATEST(forward_event_count,%s),forward_metrics=%s::jsonb,
status=CASE
 WHEN %s>=forward_deadline_at AND GREATEST(forward_event_count,%s)<3 THEN 'insufficient_evidence'
 WHEN %s>=forward_started_at+INTERVAL '30 days' AND GREATEST(forward_event_count,%s)>=3
      AND COALESCE((%s::jsonb->>'passed')::boolean,FALSE) THEN 'holdout_ready'
 WHEN %s>=forward_started_at+INTERVAL '30 days' AND GREATEST(forward_event_count,%s)>=3 THEN 'rejected'
 ELSE status END,
finished_at=CASE WHEN (%s>=forward_deadline_at AND GREATEST(forward_event_count,%s)<3)
 OR (%s>=forward_started_at+INTERVAL '30 days' AND GREATEST(forward_event_count,%s)>=3
     AND NOT COALESCE((%s::jsonb->>'passed')::boolean,FALSE)) THEN %s ELSE finished_at END,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='waiting_forward'
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                event_count,
                json.dumps(metrics),
                now,
                event_count,
                now,
                event_count,
                json.dumps(metrics),
                now,
                event_count,
                now,
                event_count,
                now,
                event_count,
                json.dumps(metrics),
                now,
                now,
                campaign_id,
                owner_account_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def record_paper_forward(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    lease_token: UUID,
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Accept only evidence tied to the persisted Paper sandbox and locked candidate."""
    sandbox_id = UUID(str(evidence["sandbox_id"]))
    candidate_id = UUID(str(evidence["candidate_id"]))
    paper_status = str(evidence["status"])
    status_map = {
        "observing": "waiting_forward",
        "passed": "holdout_ready",
        "failed": "rejected",
        "insufficient_evidence": "insufficient_evidence",
    }
    if paper_status not in status_map:
        raise ValueError("unsupported Paper Forward status")
    campaign_finished = paper_status in {"failed", "insufficient_evidence"}
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET status=%s,forward_event_count=%s,
forward_metrics=%s::jsonb,finished_at=%s,state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='waiting_forward'
AND forward_sandbox_id=%s AND locked_candidate_id=%s
AND lease_token=%s AND lease_expires_at>=clock_timestamp()
AND EXISTS(SELECT 1 FROM paper_evolution_forward_sandboxes s
 WHERE s.sandbox_id=%s AND s.campaign_id=evolution_campaigns.campaign_id
 AND s.candidate_id=evolution_campaigns.locked_candidate_id)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                status_map[paper_status],
                int(evidence.get("event_count") or 0),
                json.dumps({"paper_forward": evidence}),
                now if campaign_finished else None,
                now,
                campaign_id,
                owner_account_id,
                sandbox_id,
                candidate_id,
                lease_token,
                sandbox_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def reserve_holdout(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Irreversibly consume access before any sealed bars are evaluated."""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET holdout_consumed_at=%s,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='holdout_ready'
AND locked_candidate_id IS NOT NULL AND holdout_consumed_at IS NULL
RETURNING {_CAMPAIGN_COLUMNS}""",
            (now, now, campaign_id, owner_account_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def reserve_holdout_attempt(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
) -> dict[str, Any] | None:
    """Reserve the campaign's only holdout attempt under the campaign fencing token."""
    now = datetime.now(UTC)
    attempt_id = uuid4()
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                f"""INSERT INTO evolution_holdout_attempts(
attempt_id,campaign_id,owner_account_id,candidate_id)
SELECT %s,c.campaign_id,c.owner_account_id,c.locked_candidate_id
FROM evolution_campaigns c
WHERE c.campaign_id=%s AND c.owner_account_id=%s AND c.status='holdout_ready'
AND c.locked_candidate_id IS NOT NULL AND c.holdout_consumed_at IS NULL
AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp()
ON CONFLICT(campaign_id) DO NOTHING
RETURNING {_HOLDOUT_ATTEMPT_COLUMNS}""",
                (attempt_id, campaign_id, owner_account_id, lease_token),
            )
            attempt = await cur.fetchone()
            if attempt is not None:
                await cur.execute(
                    """UPDATE evolution_campaigns SET holdout_consumed_at=%s,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='holdout_ready'
AND locked_candidate_id=%s AND holdout_consumed_at IS NULL""",
                    (
                        now,
                        now,
                        campaign_id,
                        owner_account_id,
                        attempt["candidate_id"],
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("holdout reservation lost compare-and-swap")
                return dict(attempt)
            await cur.execute(
                f"""SELECT {_HOLDOUT_ATTEMPT_COLUMNS} FROM evolution_holdout_attempts
WHERE campaign_id=%s AND owner_account_id=%s""",
                (campaign_id, owner_account_id),
            )
            existing = await cur.fetchone()
    return dict(existing) if existing is not None else None


async def release_holdout_attempt(
    conn: AsyncConnection,
    attempt_id: UUID,
    owner_account_id: UUID,
    fencing_token: UUID,
) -> bool:
    """Release a transiently failed execution while preserving the same attempt identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_holdout_attempts SET status='reserved',fencing_token=NULL,
lease_expires_at=NULL,updated_at=NOW()
WHERE attempt_id=%s AND owner_account_id=%s AND status='running'
AND fencing_token=%s AND lease_expires_at>=clock_timestamp()""",
            (attempt_id, owner_account_id, fencing_token),
        )
        return cur.rowcount == 1


async def claim_holdout_attempt(
    conn: AsyncConnection,
    attempt_id: UUID,
    owner_account_id: UUID,
    *,
    ttl_s: int,
) -> dict[str, Any] | None:
    """Claim or resume the same attempt after its prior execution lease expires."""
    token = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_holdout_attempts SET status='running',fencing_token=%s,
lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
started_at=COALESCE(started_at,clock_timestamp()),updated_at=clock_timestamp()
WHERE attempt_id=%s AND owner_account_id=%s
AND (status='reserved' OR (status='running' AND lease_expires_at<clock_timestamp()))
RETURNING {_HOLDOUT_ATTEMPT_COLUMNS}""",
            (
                token,
                ttl_s,
                attempt_id,
                owner_account_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def holdout_attempt_input(
    conn: AsyncConnection,
    attempt_id: UUID,
    owner_account_id: UUID,
    fencing_token: UUID,
) -> dict[str, Any] | None:
    """Load the pre-committed candidate only for the active attempt fencing token."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT a.attempt_id,a.campaign_id,a.candidate_id,a.fencing_token,
i.source_code,i.source_hash,h.spec
FROM evolution_holdout_attempts a
JOIN evolution_implementations i ON i.implementation_id=a.candidate_id
 AND i.campaign_id=a.campaign_id
JOIN evolution_hypotheses h ON h.hypothesis_id=i.hypothesis_id
WHERE a.attempt_id=%s AND a.owner_account_id=%s AND a.status='running'
AND a.fencing_token=%s AND a.lease_expires_at>=clock_timestamp()""",
            (attempt_id, owner_account_id, fencing_token),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def finalize_holdout_attempt(
    conn: AsyncConnection,
    attempt_id: UUID,
    owner_account_id: UUID,
    *,
    fencing_token: UUID,
    passed: bool,
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Commit terminal evidence once, fenced to the currently running attempt."""
    now = datetime.now(UTC)
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                f"""UPDATE evolution_holdout_attempts SET status=%s,passed=%s,evidence=%s::jsonb,
finished_at=%s,lease_expires_at=NULL,updated_at=%s
WHERE attempt_id=%s AND owner_account_id=%s AND status='running'
AND fencing_token=%s AND lease_expires_at>=clock_timestamp()
RETURNING {_HOLDOUT_ATTEMPT_COLUMNS}""",
                (
                    "succeeded" if passed else "failed",
                    passed,
                    json.dumps(evidence),
                    now,
                    now,
                    attempt_id,
                    owner_account_id,
                    fencing_token,
                ),
            )
            attempt = await cur.fetchone()
            if attempt is None:
                return None
            metrics = {"sealed_holdout": evidence, "holdout_passed": passed}
            await cur.execute(
                f"""UPDATE evolution_campaigns SET status=%s,
forward_metrics=COALESCE(forward_metrics,'{{}}'::jsonb)||%s::jsonb,finished_at=%s,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='holdout_ready'
AND locked_candidate_id=%s AND holdout_consumed_at IS NOT NULL
RETURNING {_CAMPAIGN_COLUMNS}""",
                (
                    "graduated" if passed else "rejected",
                    json.dumps(metrics),
                    now,
                    now,
                    attempt["campaign_id"],
                    owner_account_id,
                    attempt["candidate_id"],
                ),
            )
            campaign = await cur.fetchone()
            if campaign is None:
                raise RuntimeError("holdout finalization lost campaign compare-and-swap")
    return dict(campaign)


async def finalize_holdout(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    *,
    passed: bool,
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Finish the already-consumed sealed evaluation without allowing a second access."""
    now = datetime.now(UTC)
    metrics = {"sealed_holdout": evidence, "holdout_passed": passed}
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET status=%s,
forward_metrics=COALESCE(forward_metrics,'{{}}'::jsonb)||%s::jsonb,finished_at=%s,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='holdout_ready'
AND locked_candidate_id IS NOT NULL AND holdout_consumed_at IS NOT NULL
AND finished_at IS NULL
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                "graduated" if passed else "rejected",
                json.dumps(metrics),
                now,
                now,
                campaign_id,
                owner_account_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def locked_implementation(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Return the locked source and its DSL only inside the Evolver trust boundary."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT i.implementation_id,i.source_code,i.source_hash,h.spec
FROM evolution_campaigns c
JOIN evolution_implementations i ON i.implementation_id=c.locked_candidate_id
 AND i.campaign_id=c.campaign_id
JOIN evolution_hypotheses h ON h.hypothesis_id=i.hypothesis_id
WHERE c.campaign_id=%s AND c.owner_account_id=%s AND c.status='holdout_ready'
AND c.holdout_consumed_at IS NULL""",
            (campaign_id, owner_account_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def adopt_graduated(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Adopt the locked source as a non-runner-eligible owner asset."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT c.locked_candidate_id,c.forward_metrics,e.source_code
FROM evolution_campaigns c JOIN evolution_implementations e
ON e.implementation_id=c.locked_candidate_id AND e.campaign_id=c.campaign_id
WHERE c.campaign_id=%s AND c.owner_account_id=%s AND c.status='graduated'""",
            (campaign_id, owner_account_id),
        )
        row = await cur.fetchone()
        if row is None or not row["source_code"]:
            return None
        source_hash = hashlib.sha256(row["source_code"].encode()).hexdigest()
        await cur.execute(
            """INSERT INTO strategy_artifacts(artifact_id,source_hash,source_code,compiler_version)
VALUES(%s,%s,%s,'event-strategy-compiler-v1')
ON CONFLICT(source_hash) DO UPDATE SET source_hash=EXCLUDED.source_hash
RETURNING artifact_id""",
            (uuid4(), source_hash, row["source_code"]),
        )
        artifact = await cur.fetchone()
        assert artifact is not None
        evidence = row["forward_metrics"] or {}
        holdout_evidence = evidence.get("sealed_holdout") or {}
        evidence_grade = "limited" if holdout_evidence.get("limited_evidence", True) else "standard"
        await cur.execute(
            """INSERT INTO strategy_adoptions(
adoption_id,artifact_id,owner_account_id,campaign_id,evidence_grade,status,runner_eligible,evidence)
VALUES(%s,%s,%s,%s,%s,'experimental',FALSE,%s::jsonb)
ON CONFLICT(owner_account_id,artifact_id) DO UPDATE SET evidence=EXCLUDED.evidence,
evidence_grade=EXCLUDED.evidence_grade
RETURNING adoption_id,artifact_id,owner_account_id,campaign_id,evidence_grade,status,
runner_eligible,evidence,adopted_at""",
            (
                uuid4(),
                artifact["artifact_id"],
                owner_account_id,
                campaign_id,
                evidence_grade,
                json.dumps(evidence),
            ),
        )
        adoption = await cur.fetchone()
    return dict(adoption) if adoption else None


async def list_adoptions(
    conn: AsyncConnection,
    owner_account_id: UUID,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """List owner-scoped experimental assets without returning executable source."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT a.adoption_id,a.artifact_id,a.owner_account_id,a.campaign_id,
a.evidence_grade,a.status,a.runner_eligible,a.evidence,a.adopted_at,
s.source_hash,s.compiler_version,c.status AS campaign_status
FROM strategy_adoptions a JOIN strategy_artifacts s USING(artifact_id)
LEFT JOIN evolution_campaigns c USING(campaign_id)
WHERE a.owner_account_id=%s ORDER BY a.adopted_at DESC,a.adoption_id DESC LIMIT %s""",
            (owner_account_id, limit),
        )
        return [dict(row) for row in await cur.fetchall()]


__all__ = [
    "acquire_lease",
    "add_llm_cost",
    "adopt_graduated",
    "advance_generation",
    "assert_active_lease",
    "best_implementation",
    "claim_holdout_attempt",
    "claim_next_campaign",
    "finalize_holdout",
    "finalize_holdout_attempt",
    "find_cached_implementation",
    "get_campaign",
    "holdout_attempt_input",
    "insert_campaign",
    "insert_hypotheses",
    "insert_implementation",
    "list_adoptions",
    "list_campaigns",
    "list_generation_implementations",
    "lock_champion",
    "locked_implementation",
    "record_forward",
    "record_paper_forward",
    "release_holdout_attempt",
    "renew_lease",
    "replace_generation_hypotheses",
    "reserve_holdout",
    "reserve_holdout_attempt",
    "start_forward_sandbox",
    "transition",
    "update_hypothesis_scores",
    "update_implementation",
]

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
hypothesis_budget,implementations_per_hypothesis,max_generations,event_snapshot_id,frozen_config,
llm_snapshot,llm_config_digest,llm_credential_grant,llm_cost_usd,
locked_candidate_id,holdout_consumed_at,forward_started_at,forward_deadline_at,
forward_event_count,forward_metrics,failure_code,failure_message,lease_owner,lease_token,
lease_expires_at,state_version,created_at,updated_at,finished_at"""
_HYPOTHESIS_COLUMNS = """hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,parent_ids,
spec,spec_hash,upper_credit,novelty_score,pareto_rank,selected,created_at"""
_IMPLEMENTATION_COLUMNS = """implementation_id,campaign_id,hypothesis_id,generation,profile,
source_code,source_hash,outcome,fitness,validation_metrics,event_metrics,evidence_quality,
novelty_score,fdr_pass,error_code,error_message,created_at,updated_at"""


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
    llm_credential_grant: str,
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
) -> dict[str, Any] | None:
    """Load one owner-scoped campaign with generation-level projection."""
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
        await cur.execute(
            f"""SELECT {_IMPLEMENTATION_COLUMNS} FROM evolution_implementations
WHERE campaign_id=%s ORDER BY generation,hypothesis_id,profile""",
            (campaign_id,),
        )
        campaign["implementations"] = [dict(item) for item in await cur.fetchall()]
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
) -> dict[str, Any]:
    """Insert one deterministic lower-level implementation idempotently."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO evolution_implementations(
implementation_id,campaign_id,hypothesis_id,generation,profile,source_code,source_hash)
VALUES(%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT(hypothesis_id,profile) DO UPDATE SET updated_at=NOW()
RETURNING {_IMPLEMENTATION_COLUMNS}""",
            (uuid4(), campaign_id, hypothesis_id, generation, profile, source_code, source_hash),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def update_implementation(
    conn: AsyncConnection,
    implementation_id: UUID,
    *,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist evaluation evidence without changing source identity."""
    updates = {**values, "updated_at": datetime.now(UTC)}
    assignments = ",".join(f"{key}=%s" for key in updates)
    params = [json.dumps(value) if isinstance(value, dict) else value for value in updates.values()]
    params.append(implementation_id)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_implementations SET {assignments}
WHERE implementation_id=%s RETURNING {_IMPLEMENTATION_COLUMNS}""",
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


async def update_hypothesis_scores(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    scores: list[dict[str, Any]],
) -> None:
    """Persist upper credit, novelty, rank, and selection flags."""
    async with conn.cursor() as cur:
        for score in scores:
            await cur.execute(
                """UPDATE evolution_hypotheses SET upper_credit=%s,novelty_score=%s,
pareto_rank=%s,selected=%s WHERE campaign_id=%s AND generation=%s AND hypothesis_id=%s""",
                (
                    score["upper_credit"],
                    score["novelty_score"],
                    score["pareto_rank"],
                    score["selected"],
                    campaign_id,
                    generation,
                    score["hypothesis_id"],
                ),
            )


async def add_llm_cost(
    conn: AsyncConnection,
    campaign_id: UUID,
    amount_usd: float,
) -> None:
    """Atomically append measured proposer cost without storing prompts or credentials."""
    if amount_usd < 0:
        raise ValueError("LLM cost cannot be negative")
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_campaigns SET llm_cost_usd=llm_cost_usd+%s,
state_version=state_version+1,updated_at=NOW() WHERE campaign_id=%s""",
            (amount_usd, campaign_id),
        )


async def replace_generation_hypotheses(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    hypotheses: list[HypothesisSpec],
) -> None:
    """Replace an unevaluated scaffold with two-call Agent proposals atomically."""
    async with conn.cursor() as cur:
        await cur.execute(
            """DELETE FROM evolution_hypotheses h WHERE h.campaign_id=%s AND h.generation=%s
AND NOT EXISTS(SELECT 1 FROM evolution_implementations i
 WHERE i.campaign_id=h.campaign_id AND i.generation=h.generation)""",
            (campaign_id, generation),
        )
        if cur.rowcount == 0:
            return
    await insert_hypotheses(conn, campaign_id, generation, hypotheses)


async def insert_hypotheses(
    conn: AsyncConnection,
    campaign_id: UUID,
    generation: int,
    hypotheses: list[HypothesisSpec],
) -> None:
    """Append exactly one immutable next generation."""
    async with conn.cursor() as cur:
        for slot, spec in enumerate(hypotheses):
            await cur.execute(
                f"""INSERT INTO evolution_hypotheses(
hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,parent_ids,spec,spec_hash)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
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
                ),
            )


async def advance_generation(
    conn: AsyncConnection,
    campaign_id: UUID,
    *,
    current_generation: int,
    next_generation: int,
) -> bool:
    """Advance only once after every implementation in the current generation is terminal."""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE evolution_campaigns c SET active_generation=%s,state_version=state_version+1,
updated_at=NOW() WHERE campaign_id=%s AND status='replaying' AND active_generation=%s
AND NOT EXISTS(SELECT 1 FROM evolution_implementations i WHERE i.campaign_id=c.campaign_id
AND i.generation=%s AND i.outcome='pending')""",
            (next_generation, campaign_id, current_generation, current_generation),
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
) -> dict[str, Any] | None:
    """Compare-and-swap campaign state and increment its projection version."""
    updates = dict(values or {})
    if to_status in {"graduated", "rejected", "insufficient_evidence", "failed", "aborted"}:
        updates["llm_credential_grant"] = None
    updates.update({"status": to_status, "updated_at": datetime.now(UTC)})
    assignments = ",".join(f"{key}=%s" for key in updates)
    params = [json.dumps(value) if isinstance(value, dict) else value for value in updates.values()]
    params.extend([campaign_id, owner_account_id, list(from_statuses)])
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET {assignments},state_version=state_version+1
WHERE campaign_id=%s AND owner_account_id=%s AND status=ANY(%s)
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
    now = datetime.now(UTC)
    token = uuid4()
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET lease_owner=%s,lease_token=%s,
lease_expires_at=%s,state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND (lease_expires_at IS NULL OR lease_expires_at<%s OR lease_owner=%s)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (worker_id, token, now + timedelta(seconds=ttl_s), now, campaign_id, now, worker_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def claim_next_campaign(
    conn: AsyncConnection,
    *,
    worker_id: str,
    ttl_s: int = 60,
) -> dict[str, Any] | None:
    """Claim one replaying campaign with SKIP LOCKED and a fresh fencing token."""
    now = datetime.now(UTC)
    token = uuid4()
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT campaign_id FROM evolution_campaigns
WHERE status='replaying' AND (lease_expires_at IS NULL OR lease_expires_at<NOW())
ORDER BY updated_at,campaign_id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            picked = await cur.fetchone()
            if picked is None:
                return None
            await cur.execute(
                f"""UPDATE evolution_campaigns SET lease_owner=%s,lease_token=%s,
lease_expires_at=%s,updated_at=%s,state_version=state_version+1
WHERE campaign_id=%s RETURNING {_CAMPAIGN_COLUMNS}""",
                (worker_id, token, now + timedelta(seconds=ttl_s), now, picked["campaign_id"]),
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
            """UPDATE evolution_campaigns SET lease_expires_at=%s,updated_at=%s
WHERE campaign_id=%s AND lease_owner=%s AND lease_token=%s AND lease_expires_at>=NOW()""",
            (
                datetime.now(UTC) + timedelta(seconds=ttl_s),
                datetime.now(UTC),
                campaign_id,
                worker_id,
                lease_token,
            ),
        )
        return cur.rowcount == 1


async def lock_champion(
    conn: AsyncConnection,
    campaign_id: UUID,
    owner_account_id: UUID,
    candidate_id: UUID,
) -> dict[str, Any] | None:
    """Irreversibly lock one champion and start its 30-90 day forward window."""
    now = datetime.now(UTC)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""UPDATE evolution_campaigns SET status='waiting_forward',locked_candidate_id=%s,
forward_started_at=%s,forward_deadline_at=%s,active_generation=max_generations,
llm_credential_grant=NULL,
state_version=state_version+1,updated_at=%s
WHERE campaign_id=%s AND owner_account_id=%s AND status='replaying'
AND active_generation=max_generations AND locked_candidate_id IS NULL
AND EXISTS(SELECT 1 FROM evolution_implementations i
 WHERE i.implementation_id=%s AND i.campaign_id=evolution_campaigns.campaign_id
 AND i.generation=evolution_campaigns.max_generations AND i.outcome='succeeded'
 AND i.fdr_pass IS TRUE)
RETURNING {_CAMPAIGN_COLUMNS}""",
            (
                candidate_id,
                now,
                now + timedelta(days=90),
                now,
                campaign_id,
                owner_account_id,
                candidate_id,
            ),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


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
    "best_implementation",
    "claim_next_campaign",
    "finalize_holdout",
    "find_cached_implementation",
    "get_campaign",
    "insert_campaign",
    "insert_hypotheses",
    "insert_implementation",
    "list_adoptions",
    "list_campaigns",
    "list_generation_implementations",
    "lock_champion",
    "locked_implementation",
    "record_forward",
    "renew_lease",
    "replace_generation_hypotheses",
    "reserve_holdout",
    "transition",
    "update_hypothesis_scores",
    "update_implementation",
]

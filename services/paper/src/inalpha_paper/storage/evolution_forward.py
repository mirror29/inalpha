"""Paper-owned storage for isolated E2 Forward sandboxes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

_COLUMNS = """sandbox_id,campaign_id,candidate_id,owner_account_id,asset_id,venue,symbol,
timeframe,source_hash,hypothesis_spec,frozen_versions,status,started_at,deadline_at,event_count,
metrics,risk_alerts,data_quality_alerts,evidence_version,evidence_digest,finished_at,created_at,
updated_at"""

_WORKER_COLUMNS = f"{_COLUMNS},source_code,lease_owner,lease_token,lease_expires_at"


async def create_sandbox(
    conn: AsyncConnection,
    *,
    campaign_id: UUID,
    candidate_id: UUID,
    owner_account_id: UUID,
    asset_id: str,
    venue: str,
    symbol: str,
    timeframe: str,
    expected_source_hash: str,
    frozen_versions: dict[str, Any],
) -> dict[str, Any] | None:
    """Freeze the locked candidate into Paper without touching account/trading tables."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""INSERT INTO paper_evolution_forward_sandboxes(
sandbox_id,campaign_id,candidate_id,owner_account_id,asset_id,venue,symbol,timeframe,
source_code,source_hash,hypothesis_spec,frozen_versions)
SELECT %s,c.campaign_id,i.implementation_id,c.owner_account_id,%s,%s,%s,%s,
i.source_code,i.source_hash,h.spec,%s::jsonb
FROM evolution_campaigns c
JOIN evolution_implementations i ON i.implementation_id=c.locked_candidate_id
 AND i.campaign_id=c.campaign_id
JOIN evolution_hypotheses h ON h.hypothesis_id=i.hypothesis_id
WHERE c.campaign_id=%s AND c.owner_account_id=%s AND c.status='candidate_locked'
AND i.implementation_id=%s AND i.source_hash=%s
ON CONFLICT(campaign_id) DO NOTHING
RETURNING {_COLUMNS}""",
            (
                uuid4(),
                asset_id,
                venue,
                symbol,
                timeframe,
                json.dumps(frozen_versions),
                campaign_id,
                owner_account_id,
                candidate_id,
                expected_source_hash,
            ),
        )
        row = await cur.fetchone()
        if row is not None:
            return dict(row)
        await cur.execute(
            f"""SELECT {_COLUMNS} FROM paper_evolution_forward_sandboxes
WHERE campaign_id=%s AND owner_account_id=%s""",
            (campaign_id, owner_account_id),
        )
        existing = await cur.fetchone()
    return dict(existing) if existing is not None else None


async def get_sandbox(
    conn: AsyncConnection,
    sandbox_id: UUID,
    owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Read signed-evidence projection without exposing candidate source code."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_COLUMNS} FROM paper_evolution_forward_sandboxes
WHERE sandbox_id=%s AND owner_account_id=%s""",
            (sandbox_id, owner_account_id),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def claim_next_sandbox(
    conn: AsyncConnection,
    *,
    worker_id: str,
    ttl_s: int,
    poll_interval_s: int,
) -> dict[str, Any] | None:
    """Claim one observing sandbox with SKIP LOCKED and a fresh fencing token."""
    now = datetime.now(UTC)
    token = uuid4()
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT sandbox_id FROM paper_evolution_forward_sandboxes
WHERE status='observing' AND (lease_expires_at IS NULL OR lease_expires_at<NOW())
AND (evidence_version=0 OR updated_at<=NOW()-(%s * INTERVAL '1 second'))
ORDER BY updated_at,sandbox_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                (poll_interval_s,),
            )
            picked = await cur.fetchone()
            if picked is None:
                return None
            await cur.execute(
                f"""UPDATE paper_evolution_forward_sandboxes SET lease_owner=%s,lease_token=%s,
lease_expires_at=%s,updated_at=%s WHERE sandbox_id=%s RETURNING {_WORKER_COLUMNS}""",
                (worker_id, token, now + timedelta(seconds=ttl_s), now, picked["sandbox_id"]),
            )
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def append_observations(
    conn: AsyncConnection,
    *,
    sandbox_id: UUID,
    lease_token: UUID,
    bars: list[dict[str, Any]],
    facts: list[dict[str, Any]],
) -> bool:
    """Persist immutable inputs exactly once while the worker fencing token is current."""
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT 1 FROM paper_evolution_forward_sandboxes
WHERE sandbox_id=%s AND status='observing' AND lease_token=%s AND lease_expires_at>=NOW()
FOR UPDATE""",
                (sandbox_id, lease_token),
            )
            if await cur.fetchone() is None:
                return False
            for bar in bars:
                await cur.execute(
                    """INSERT INTO paper_evolution_forward_bars(
sandbox_id,bar_open_at,bar_known_at,payload,payload_sha256)
VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT(sandbox_id,bar_open_at) DO NOTHING""",
                    (
                        sandbox_id,
                        bar["bar_open_at"],
                        bar["bar_known_at"],
                        json.dumps(bar["payload"], sort_keys=True, separators=(",", ":")),
                        bar["payload_sha256"],
                    ),
                )
            for fact in facts:
                await cur.execute(
                    """INSERT INTO paper_evolution_forward_facts(
sandbox_id,fact_id,available_at) VALUES(%s,%s,%s)
ON CONFLICT(sandbox_id,fact_id) DO NOTHING""",
                    (sandbox_id, fact["fact_id"], fact["available_at"]),
                )
    return True


async def load_replay_inputs(
    conn: AsyncConnection,
    sandbox_id: UUID,
    lease_token: UUID,
) -> dict[str, Any] | None:
    """Load only the fenced sandbox and its immutable normalized observations."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""SELECT {_WORKER_COLUMNS} FROM paper_evolution_forward_sandboxes
WHERE sandbox_id=%s AND status='observing' AND lease_token=%s AND lease_expires_at>=NOW()""",
            (sandbox_id, lease_token),
        )
        sandbox = await cur.fetchone()
        if sandbox is None:
            return None
        await cur.execute(
            """SELECT payload FROM paper_evolution_forward_bars
WHERE sandbox_id=%s ORDER BY bar_open_at""",
            (sandbox_id,),
        )
        bars = [dict(row["payload"]) for row in await cur.fetchall()]
        await cur.execute(
            """SELECT f.fact_id,f.raw_event_id,f.event_type,f.assets,f.asset_ids,f.actor,f.action,
f.severity,f.confidence,f.effective_at,f.available_at,f.evidence_spans,f.policy_version,f.retracted
FROM paper_evolution_forward_facts pf
JOIN market_event_facts f USING(fact_id)
WHERE pf.sandbox_id=%s ORDER BY f.available_at,f.fact_id""",
            (sandbox_id,),
        )
        facts = [dict(row) for row in await cur.fetchall()]
    return {"sandbox": dict(sandbox), "bars": bars, "facts": facts}


async def persist_replay_result(
    conn: AsyncConnection,
    *,
    sandbox_id: UUID,
    lease_token: UUID,
    status: str,
    event_count: int,
    metrics: dict[str, Any] | None,
    risk_alerts: list[str],
    data_quality_alerts: list[str],
    fills: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Append deterministic fills and atomically publish one evidence version."""
    terminal = status != "observing"
    now = datetime.now(UTC)
    async with conn.transaction():
        async with conn.cursor() as cur:
            for fill in fills:
                await cur.execute(
                    """INSERT INTO paper_evolution_forward_fills(
sandbox_id,fill_key,fact_id,filled_at,side,quantity,price,fee,slippage_cost)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT(sandbox_id,fill_key) DO NOTHING""",
                    (
                        sandbox_id,
                        fill["fill_key"],
                        fill.get("fact_id"),
                        fill["filled_at"],
                        fill["side"],
                        fill["quantity"],
                        fill["price"],
                        fill["fee"],
                        fill["slippage_cost"],
                    ),
                )
            await cur.execute(
                f"""UPDATE paper_evolution_forward_sandboxes SET status=%s,event_count=%s,
metrics=%s::jsonb,risk_alerts=%s::jsonb,data_quality_alerts=%s::jsonb,
evidence_version=evidence_version+1,finished_at=%s,lease_owner=NULL,lease_token=NULL,
lease_expires_at=NULL,updated_at=%s
WHERE sandbox_id=%s AND status='observing' AND lease_token=%s
AND lease_expires_at>=NOW() RETURNING {_COLUMNS}""",
                (
                    status,
                    event_count,
                    json.dumps(metrics) if metrics is not None else None,
                    json.dumps(risk_alerts),
                    json.dumps(data_quality_alerts),
                    now if terminal else None,
                    now,
                    sandbox_id,
                    lease_token,
                ),
            )
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def release_lease(
    conn: AsyncConnection,
    sandbox_id: UUID,
    lease_token: UUID,
) -> bool:
    """Release one transiently failed observation cycle without changing evidence."""
    async with conn.cursor() as cur:
        await cur.execute(
            """UPDATE paper_evolution_forward_sandboxes SET lease_owner=NULL,lease_token=NULL,
lease_expires_at=NULL,updated_at=NOW()
WHERE sandbox_id=%s AND status='observing' AND lease_token=%s""",
            (sandbox_id, lease_token),
        )
        return cur.rowcount == 1


__all__ = [
    "append_observations",
    "claim_next_sandbox",
    "create_sandbox",
    "get_sandbox",
    "load_replay_inputs",
    "persist_replay_result",
    "release_lease",
]

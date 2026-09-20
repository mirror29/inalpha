"""Atomic, immutable generation proposals and cost receipts under campaign fencing."""

import hashlib
import json
import math
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from ..hypothesis.models import HypothesisSpec
from . import campaigns


async def get_proposal(
    conn: AsyncConnection, campaign_id: UUID, generation: int,
) -> dict[str, Any] | None:
    """Return whether proposal work already committed, even before the first evaluation."""
    cursor = await conn.execute(
        "SELECT * FROM evolution_proposal_checkpoints WHERE campaign_id=%s AND generation=%s",
        (campaign_id, generation),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def commit_proposal(
    conn: AsyncConnection, *, campaign_id: UUID, generation: int,
    hypotheses: list[HypothesisSpec], lease_token: UUID, cost_usd: float, fallback_calls: int,
) -> bool:
    """Replace scaffolds and book cost exactly once; an expired worker cannot commit."""
    if not math.isfinite(cost_usd) or cost_usd < 0 or len(hypotheses) != 8:
        raise ValueError("proposal requires eight hypotheses and a finite nonnegative cost")
    digest = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in hypotheses],
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    async with conn.transaction():
        cursor = await conn.execute(
            """SELECT campaign_id FROM evolution_campaigns WHERE campaign_id=%s
AND status='replaying' AND active_generation=%s AND lease_token=%s
AND lease_expires_at>=clock_timestamp() FOR UPDATE""",
            (campaign_id, generation, lease_token),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("campaign proposal lease was lost")
        previous = await get_proposal(conn, campaign_id, generation)
        if previous:
            if previous["content_sha256"] != digest:
                raise RuntimeError("committed generation proposal is immutable")
            return False
        cursor = await conn.execute(
            "SELECT 1 FROM evolution_implementations WHERE campaign_id=%s AND generation=%s LIMIT 1",
            (campaign_id, generation),
        )
        if await cursor.fetchone() is not None:
            raise RuntimeError("cannot replace a generation after candidate evaluation started")
        await campaigns.replace_generation_hypotheses(
            conn, campaign_id, generation, hypotheses, lease_token,
        )
        await campaigns.add_llm_cost(conn, campaign_id, cost_usd, lease_token)
        cursor = await conn.execute(
            """INSERT INTO evolution_proposal_checkpoints(
campaign_id,generation,content_sha256,cost_usd,fallback_calls)
SELECT %s,%s,%s,%s,%s FROM evolution_campaigns
WHERE campaign_id=%s AND lease_token=%s AND lease_expires_at>=clock_timestamp()""",
            (campaign_id, generation, digest, cost_usd, fallback_calls, campaign_id, lease_token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("campaign proposal lease expired before commit")
    return True

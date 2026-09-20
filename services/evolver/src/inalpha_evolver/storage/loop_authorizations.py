"""Bounded loop authorization and pessimistic cost reservations, without model secrets."""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from inalpha_shared.errors import ConflictError
from psycopg import AsyncConnection

from ..exceptions import LoopControlError


class LoopBudgetExhausted(LoopControlError):
    code = "LOOP_BUDGET_EXHAUSTED"


async def budget_summary(
    conn: AsyncConnection, loop_id: UUID, owner_account_id: UUID,
) -> dict[str, Any] | None:
    """Expose only cost totals; absent legacy authority is unknown, not zero spend."""
    cursor = await conn.execute(
        """SELECT max_cost_usd,spent_usd,reserved_usd,
GREATEST(0,max_cost_usd-spent_usd-reserved_usd) AS available_usd
FROM evolution_loop_authorizations WHERE loop_id=%s AND owner_account_id=%s""",
        (loop_id, owner_account_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


class LoopAuthorizationUnavailable(LoopControlError):
    code = "LOOP_AUTHORIZATION_UNAVAILABLE"


async def register(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    request_digest: str,
    max_cost_usd: Decimal,
) -> dict[str, Any]:
    """Called only after verifying the signed start request, in its creation transaction."""
    if not max_cost_usd.is_finite() or max_cost_usd <= 0:
        raise ValueError("loop budget must be positive and finite")
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO evolution_loop_authorizations(loop_id,owner_account_id,
request_digest,llm_snapshot,max_cost_usd,expires_at)
SELECT l.loop_id,l.owner_account_id,%s,r.llm_snapshot,%s,clock_timestamp()+INTERVAL '90 days'
FROM evolution_loops l JOIN strategy_evo_runs r ON r.run_id=l.e1_run_id
AND r.owner_account_id=l.owner_account_id
WHERE l.loop_id=%s AND l.owner_account_id=%s
ON CONFLICT(loop_id) DO NOTHING""",
            (request_digest, max_cost_usd, loop_id, owner_account_id),
        )
        await cur.execute(
            "SELECT * FROM evolution_loop_authorizations WHERE loop_id=%s AND owner_account_id=%s",
            (loop_id, owner_account_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise LoopAuthorizationUnavailable("owner loop is missing")
    if row["request_digest"] != request_digest or row["max_cost_usd"] != max_cost_usd:
        raise ConflictError("loop authorization is immutable", code="IDEMPOTENCY_KEY_REUSED")
    return dict(row)


async def _lock_authorized(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    phase: Literal["baseline", "campaign"],
) -> dict[str, Any]:
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT a.* FROM evolution_loop_authorizations a
JOIN evolution_loops l USING(loop_id)
WHERE a.loop_id=%s AND a.owner_account_id=%s AND l.owner_account_id=%s
AND a.revoked_at IS NULL AND a.expires_at>clock_timestamp()
AND l.status IN ('target_resolved','baseline_ready','campaign_running')
AND ((%s='baseline' AND l.campaign_id IS NULL AND l.lease_token=%s
      AND l.lease_expires_at>=clock_timestamp())
  OR (%s='campaign' AND EXISTS(SELECT 1 FROM evolution_campaigns c
      WHERE c.campaign_id=l.campaign_id AND c.owner_account_id=l.owner_account_id
      AND c.status='replaying' AND c.lease_token=%s AND c.lease_expires_at>=clock_timestamp())))
FOR UPDATE OF a,l""",
            (loop_id, owner_account_id, owner_account_id, phase, lease_token, phase, lease_token),
        )
        row = await cur.fetchone()
        if row is not None and phase == "campaign":
            await cur.execute(
                """SELECT c.campaign_id FROM evolution_campaigns c
JOIN evolution_loops l ON l.campaign_id=c.campaign_id
WHERE l.loop_id=%s AND c.owner_account_id=%s AND c.lease_token=%s
AND c.status='replaying' AND c.lease_expires_at>=clock_timestamp() FOR UPDATE OF c""",
                (loop_id, owner_account_id, lease_token),
            )
            if await cur.fetchone() is None:
                row = None
    if row is None:
        raise LoopAuthorizationUnavailable("loop authority expired, revoked, or fenced out")
    return dict(row)


async def reserve(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    phase: Literal["baseline", "campaign"],
    reservation_id: UUID,
    step_key: str,
    amount_usd: Decimal,
) -> None:
    """Reserve the full maximum before each provider attempt; lost responses stay reserved."""
    if not amount_usd.is_finite() or amount_usd <= 0:
        raise ValueError("cost reservation must be positive and finite")
    async with conn.transaction():
        authority = await _lock_authorized(
            conn,
            loop_id=loop_id,
            owner_account_id=owner_account_id,
            lease_token=lease_token,
            phase=phase,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM evolution_loop_cost_reservations WHERE reservation_id=%s",
                (reservation_id,),
            )
            existing = await cur.fetchone()
            if existing is not None:
                if (
                    existing["loop_id"] != loop_id
                    or existing["step_key"] != step_key
                    or existing["reserved_usd"] != amount_usd
                ):
                    raise ConflictError(
                        "reservation identity reused", code="LOOP_RESERVATION_CONFLICT"
                    )
                return
            if (
                authority["spent_usd"] + authority["reserved_usd"] + amount_usd
                > authority["max_cost_usd"]
            ):
                raise LoopBudgetExhausted("loop model budget exhausted")
            await cur.execute(
                "UPDATE evolution_loop_authorizations SET reserved_usd=reserved_usd+%s WHERE loop_id=%s",
                (amount_usd, loop_id),
            )
            await cur.execute(
                """INSERT INTO evolution_loop_cost_reservations(reservation_id,loop_id,step_key,reserved_usd)
VALUES(%s,%s,%s,%s)""",
                (reservation_id, loop_id, step_key, amount_usd),
            )


async def settle(
    conn: AsyncConnection,
    *,
    loop_id: UUID,
    owner_account_id: UUID,
    lease_token: UUID,
    phase: Literal["baseline", "campaign"],
    reservation_id: UUID,
    actual_usd: Decimal,
) -> None:
    """Apply one provider receipt exactly once; an uncertain attempt never gets a refund."""
    if not actual_usd.is_finite() or actual_usd < 0:
        raise ValueError("actual cost must be nonnegative and finite")
    async with conn.transaction():
        await _lock_authorized(
            conn,
            loop_id=loop_id,
            owner_account_id=owner_account_id,
            lease_token=lease_token,
            phase=phase,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM evolution_loop_cost_reservations WHERE reservation_id=%s AND loop_id=%s FOR UPDATE",
                (reservation_id, loop_id),
            )
            row = await cur.fetchone()
            if row is None or actual_usd > row["reserved_usd"]:
                raise ConflictError(
                    "cost receipt exceeds reservation", code="LOOP_COST_RECEIPT_INVALID"
                )
            if row["actual_usd"] is not None:
                if row["actual_usd"] != actual_usd:
                    raise ConflictError(
                        "cost receipt is immutable", code="LOOP_COST_RECEIPT_INVALID"
                    )
                return
            await cur.execute(
                "UPDATE evolution_loop_cost_reservations SET actual_usd=%s,settled_at=clock_timestamp() WHERE reservation_id=%s",
                (actual_usd, reservation_id),
            )
            await cur.execute(
                """UPDATE evolution_loop_authorizations SET reserved_usd=reserved_usd-%s,spent_usd=spent_usd+%s
WHERE loop_id=%s""",
                (row["reserved_usd"], actual_usd, loop_id),
            )

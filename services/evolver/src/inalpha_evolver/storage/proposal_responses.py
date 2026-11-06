"""Fenced response reuse for a frozen loop's two proposer batches per generation."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..exceptions import LoopControlError
from . import loop_authorizations as authority


class ProposalResponseUnconfirmed(LoopControlError):
    """A sent attempt cannot be silently repurchased after an interrupted response."""

    code = "LOOP_PROPOSAL_RESPONSE_UNCONFIRMED"


async def begin(
    conn: AsyncConnection, *, scope: dict[str, Any], generation: int, batch: int,
    request_digest: str, maximum: Decimal,
) -> dict[str, Any]:
    """Reserve and mark dispatch atomically, or return the already stored response."""
    if scope["phase"] != "campaign" or not 1 <= generation <= 5 or batch not in (0, 1):
        raise authority.LoopAuthorizationUnavailable("invalid proposal checkpoint scope")
    async with conn.transaction():
        await authority._lock_authorized(conn, **scope)
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "SELECT * FROM evolution_loop_proposal_responses "
                "WHERE loop_id=%s AND generation=%s AND batch=%s",
                (scope["loop_id"], generation, batch),
            )
            row = await cursor.fetchone()
        if row is not None:
            if row["request_digest"] != request_digest:
                raise authority.LoopAuthorizationUnavailable("proposal input changed after dispatch")
            if row["response"] is None:
                raise ProposalResponseUnconfirmed(
                    "Previous proposal response is unconfirmed; automatic redispatch is blocked"
                )
            return dict(row)
        reservation = uuid4()
        await authority.reserve(
            conn, **scope, reservation_id=reservation,
            step_key=f"campaign:proposal:{generation}:{batch}", amount_usd=maximum,
        )
        await conn.execute(
            "INSERT INTO evolution_loop_proposal_responses "
            "(loop_id,generation,batch,request_digest,reservation_id) VALUES(%s,%s,%s,%s,%s)",
            (scope["loop_id"], generation, batch, request_digest, reservation),
        )
        return {"reservation_id": reservation, "response": None}


async def complete(
    conn: AsyncConnection, *, scope: dict[str, Any], reservation_id: UUID,
    response: dict[str, Any], actual: Decimal | None,
) -> None:
    """Commit the provider receipt and response together under the current lease."""
    async with conn.transaction():
        await authority._lock_authorized(conn, **scope)
        if actual is not None:
            await authority.settle(conn, **scope, reservation_id=reservation_id, actual_usd=actual)
        cursor = await conn.execute(
            "UPDATE evolution_loop_proposal_responses SET response=%s,responded_at=clock_timestamp() "
            "WHERE loop_id=%s AND reservation_id=%s AND response IS NULL",
            (Jsonb(response), scope["loop_id"], reservation_id),
        )
        if cursor.rowcount != 1:
            raise authority.LoopAuthorizationUnavailable("proposal response already completed or missing")

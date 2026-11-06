"""Attempt the real checkpoint write with an old lease in the isolated business image."""

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from inalpha_evolver.storage import loop_authorizations, proposal_responses


async def main():
    positive = "--positive-control" in sys.argv
    fixture = json.loads(
        await asyncio.to_thread(Path("/probe/exchange/stale_scope.json").read_text)
    )
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn:
        assert conn.info.dbname.startswith("inalpha_container_audit_")
        await conn.execute("SET LOCAL statement_timeout='5s'")
        row = await conn.execute(
            "SELECT status,lease_token,lease_expires_at,clock_timestamp() AS observed_at FROM evolution_campaigns WHERE campaign_id=%s AND owner_account_id=%s",
            (UUID(fixture["campaign_id"]), UUID(fixture["owner"])),
        )
        live = await row.fetchone()
        assert (
            live["status"] == "replaying" and str(live["lease_token"]) == fixture["new_lease_token"]
        )
        assert (positive or str(live["lease_token"]) != fixture["old_lease_token"]) and live[
            "lease_expires_at"
        ] > live["observed_at"]
        try:
            await proposal_responses.complete(
                conn,
                scope={
                    "loop_id": UUID(fixture["loop_id"]),
                    "owner_account_id": UUID(fixture["owner"]),
                    "lease_token": UUID(fixture["old_lease_token"]),
                    "phase": "campaign",
                },
                reservation_id=UUID(fixture["reservation_id"]),
                response={"text": "offline stale response"},
                actual=Decimal("0.000001"),
            )
        except loop_authorizations.LoopAuthorizationUnavailable as exc:
            if positive:
                raise AssertionError("live scope rejected") from exc
            await conn.rollback()
            print(
                json.dumps(
                    {
                        "result": "rejected",
                        "code": exc.code,
                        "operation": "proposal_responses.complete",
                        "active_lease_at_attempt": {k: str(v) for k, v in live.items()},
                        "scope": "direct same-image storage write attempt, not an old SDK callback",
                    }
                )
            )
            return
        await conn.rollback()
        if positive:
            print(
                json.dumps(
                    {
                        "result": "accepted_rolled_back",
                        "operation": "proposal_responses.complete",
                        "active_lease_at_attempt": {k: str(v) for k, v in live.items()},
                        "reservation_id": fixture["reservation_id"],
                    }
                )
            )
            return
        raise AssertionError("stale checkpoint write accepted")


asyncio.run(main())

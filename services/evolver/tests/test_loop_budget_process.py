"""Real process death preserves model reservations; provider is an offline fixture.

This checks the budget client and PostgreSQL boundary, not the complete app,
credential redemption, or a paid provider. Committed audit rows are retained.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from inalpha_evolver.storage import loop_authorizations as auth
from inalpha_evolver.storage import loops

from .test_loop_storage import create_baseline, database_url

WORKER = r'''
import asyncio, json, os
from pathlib import Path
from uuid import UUID
from inalpha_shared.db import init_pool, close_pool
from inalpha_shared_llm.types import CacheMetrics, MutationRequest, MutationResponse
from inalpha_evolver.loop_llm import BudgetedLoopClient, LoopModelScope
from inalpha_evolver.storage.loop_authorizations import LoopBudgetExhausted

async def main():
    await init_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    scope = json.loads(os.environ["TEST_SCOPE"])
    scope = LoopModelScope(**{k: UUID(v) if k != "phase" else v for k, v in scope.items()})
    marker = Path(os.environ["TEST_MARKER"])
    class Provider:
        async def mutate(self, request):
            with marker.open("a") as output:
                output.write("provider-entered\n")
                output.flush()
                os.fsync(output.fileno())
            if os.environ["TEST_MODE"] == "lost":
                await asyncio.Event().wait()
            return MutationResponse(content="[]", cache_metrics=CacheMetrics(input_tokens=100, output_tokens=40))
    try:
        client = BudgetedLoopClient(Provider(), scope, json.loads(os.environ["TEST_PRICING"]))
        try:
            await client.mutate(MutationRequest(system_prompt="system", user_prompt="user"))
        except LoopBudgetExhausted:
            return 23
        return 0
    finally:
        await close_pool()
raise SystemExit(asyncio.run(main()))
'''


async def seed(url):
    """Commit a uniquely owned fixture so separate worker processes can observe it."""
    async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn:
        args = await create_baseline(conn, uuid4())
        loop = await loops.ensure_for_e1_run(conn, **args)
        await auth.register(
            conn, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
            request_digest="a" * 64, max_cost_usd=Decimal("0.0170304"),
        )
        lease_token = uuid4()
        await conn.execute(
            "UPDATE evolution_loops SET lease_owner='budget-process-test',lease_token=%s,"
            "lease_expires_at=clock_timestamp()+INTERVAL '120 seconds',"
            "next_attempt_at='infinity' WHERE loop_id=%s",
            (lease_token, loop["loop_id"]),
        )
        return {
            "loop_id": str(loop["loop_id"]),
            "owner_account_id": str(args["owner_account_id"]),
            "lease_token": str(lease_token), "phase": "baseline",
        }


def ledger(url, loop_id):
    """Read the durable reservation rather than relying on worker stdout."""
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5s'")
        return dict(conn.execute(
            "SELECT spent_usd,reserved_usd,"
            "(SELECT count(*) FROM evolution_loop_cost_reservations r WHERE r.loop_id=a.loop_id) "
            "AS attempts FROM evolution_loop_authorizations a WHERE loop_id=%s", (loop_id,),
        ).fetchone())


@pytest.mark.parametrize("lost_response", [True, False])
def test_budget_survives_worker_death_and_new_process(tmp_path, lost_response):
    """Unknown cost survives SIGKILL; settled cost is not settled again on restart."""
    if os.name != "posix":
        pytest.skip("requires POSIX SIGKILL")
    url = database_url()
    scope = asyncio.run(seed(url))
    from .llm_snapshot_fixtures import llm_snapshot

    marker = tmp_path / "attempts.txt"
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    env.update(
        DATABASE_URL=url, TEST_SCOPE=json.dumps(scope), TEST_MARKER=str(marker),
        TEST_MODE="lost" if lost_response else "known",
        TEST_PRICING=json.dumps(llm_snapshot()["pricing"]),
    )
    with (tmp_path / "worker.log").open("w") as log:
        worker = subprocess.Popen([sys.executable, "-c", WORKER], env=env, stdout=log, stderr=log)
        try:
            if lost_response:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if marker.exists() and marker.read_text() == "provider-entered\n":
                        break
                    assert worker.poll() is None, "worker exited before provider boundary"
                    time.sleep(0.05)
                assert marker.exists() and marker.read_text() == "provider-entered\n", (
                    "provider boundary not reached"
                )
                worker.kill()
                assert worker.wait(timeout=10) == -9
            else:
                assert worker.wait(timeout=20) == 0
            before = ledger(url, scope["loop_id"])
            assert before == {
                "spent_usd": Decimal("0") if lost_response else Decimal("0.000078"),
                "reserved_usd": Decimal("0.0170304") if lost_response else Decimal("0"),
                "attempts": 1,
            }
            restarted = subprocess.run(
                [sys.executable, "-c", WORKER], env=env, stdout=log, stderr=log, timeout=20,
            )
            assert restarted.returncode == 23
            assert ledger(url, scope["loop_id"]) == before
            assert marker.read_text().splitlines() == ["provider-entered"]
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)

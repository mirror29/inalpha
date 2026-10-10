"""Approval attribution must not cross owners even when operation IDs are known."""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from inalpha_shared.usage import UsageIdentity
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from inalpha_evolver.usage import EvolverUsageStore

URL = os.environ.get("INALPHA_USAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="dedicated ledger database not configured")


@pytest.mark.asyncio
async def test_evolution_attribution_uses_owned_persisted_association(monkeypatch):
    assert URL and URL.endswith("/inalpha_migration_usage208_test") and "@localhost:" in URL

    @asynccontextmanager
    async def connection():
        async with await AsyncConnection.connect(URL, row_factory=dict_row) as conn:
            yield conn

    monkeypatch.setattr("inalpha_evolver.usage.get_conn", connection)
    operation, invocation, chat_call = uuid4(), uuid4(), uuid4()
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO chat_usage_receipts(call_id,invocation_id,auth_sub,step_number,call_source) VALUES(%s,%s,'alice',0,'chat')",
            (chat_call, invocation),
        )
        await conn.execute(
            "INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name) VALUES('alice',%s,%s,'evolver.run_evolution')",
            (operation, invocation),
        )
    store = EvolverUsageStore(URL, str(operation), None)
    for owner in ("alice", "bob"):
        call = str(uuid4())
        await store.begin(
            UsageIdentity(owner, "evolver", str(uuid4()), "openai", "fixture"),
            call,
            call,
            0,
            {},
            "test",
        )
        async with connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT parent_operation_id FROM llm_usage_calls WHERE call_id=%s", (call,)
                )
            ).fetchone()
            assert row["parent_operation_id"] == (str(invocation) if owner == "alice" else None)

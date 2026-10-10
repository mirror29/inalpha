"""Optional database verification, restricted to this task's dedicated test database."""

import os
from uuid import uuid4

import pytest
from inalpha_shared.db import close_pool, get_conn, init_pool
from inalpha_shared.usage import PostgresUsageStore, UsageIdentity, UsageRecorder
from psycopg import AsyncConnection
from psycopg.rows import dict_row

URL = os.environ.get("INALPHA_USAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="dedicated ledger test DB not configured")


async def test_owner_bound_idempotent_settlement():
    assert URL and URL.endswith("/inalpha_migration_usage208_test") and "@localhost:" in URL
    store = PostgresUsageStore(URL, connection_factory=get_conn)
    identity = UsageIdentity("usage-test-alice", "research", str(uuid4()), "openai", "fixture")
    call, logical = str(uuid4()), str(uuid4())
    await store.begin(identity, call, logical, 0, {}, "test")
    receipt = {
        "status": "completed",
        "usage_status": "known",
        "input_tokens": 1,
        "output_tokens": 2,
        "cached_input_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "estimated_cost_usd": None,
        "response_model": "fixture",
        "finish_reason": "stop",
        "latency_ms": 1,
    }
    await store.settle(
        UsageIdentity("bob", "research", identity.operation_id, "openai", "fixture"), call, receipt
    )
    async with await AsyncConnection.connect(URL, row_factory=dict_row) as conn:
        row = await (
            await conn.execute("SELECT * FROM llm_usage_calls WHERE call_id=%s", (call,))
        ).fetchone()
        assert row["status"] == "pending"
    await store.settle(identity, call, receipt)
    await store.settle(identity, call, {**receipt, "input_tokens": 99})
    async with await AsyncConnection.connect(URL, row_factory=dict_row) as conn:
        row = await (
            await conn.execute("SELECT * FROM llm_usage_calls WHERE call_id=%s", (call,))
        ).fetchone()
        assert row["input_tokens"] == 1
        assert row["estimated_cost_usd"] is None
    assert await store.owned_operation("bob", identity.operation_id) is None
    assert (
        await store.owned_operation(identity.auth_sub, identity.operation_id)
        == identity.operation_id
    )


async def test_real_store_records_invalid_response_usage():
    assert URL and URL.endswith("/inalpha_migration_usage208_test") and "@localhost:" in URL
    identity = UsageIdentity("usage-test-alice", "research", str(uuid4()), "openai", "fixture")
    recorder = UsageRecorder(identity, PostgresUsageStore(URL, connection_factory=get_conn))

    async def invoke():
        return {"usage": {"prompt_tokens": 5, "completion_tokens": 3}, "choices": []}

    def invalid(response):
        raise ValueError("invalid structured output")

    with pytest.raises(ValueError):
        await recorder.request(invoke, validate=invalid)
    async with await AsyncConnection.connect(URL, row_factory=dict_row) as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM llm_usage_calls WHERE operation_id=%s", (identity.operation_id,)
            )
        ).fetchone()
        assert row["status"] == "invalid"
        assert row["input_tokens"] == 5
        assert row["usage_status"] == "known"


@pytest.fixture(autouse=True)
async def ledger_pool():
    await init_pool(URL, min_size=0, max_size=2, timeout=5)
    try:
        yield
    finally:
        await close_pool()


async def test_same_subject_reads_all_service_calls_without_prefix_rewriting():
    owner, other = f"user:{uuid4()}", str(uuid4())
    store = PostgresUsageStore(URL, connection_factory=get_conn)
    for service in ("chat", "research", "evolver"):
        operation, call = str(uuid4()), str(uuid4())
        await store.begin(
            UsageIdentity(owner, service, operation, "openai", "fixture"), call, call, 0, {}, "test"
        )
        assert await store.owned_operation(owner, operation) == operation
        assert await store.owned_operation(other, operation) is None
    async with get_conn() as conn:
        rows = await (
            await conn.execute("SELECT service FROM llm_usage_calls WHERE auth_sub=%s", (owner,))
        ).fetchall()
    assert {row["service"] for row in rows} == {"chat", "research", "evolver"}

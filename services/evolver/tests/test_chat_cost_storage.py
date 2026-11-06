"""Real approval/call/run joins preserve owner isolation and unknown old costs."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest

from inalpha_evolver.storage import chat_costs

from .test_loop_storage import create_baseline, loop_database  # noqa: F401


async def associate(conn, args, invocation, auth_sub=None):
    operation = uuid4()
    owner = auth_sub or args["requested_by_sub"]
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE strategy_evo_runs SET idempotency_key=%s WHERE run_id=%s",
            (str(operation), args["e1_run_id"]),
        )
        await cur.execute(
            """INSERT INTO evolution_approval_operations
(operation_id,auth_sub,session_id,tool_name,input_digest,approved_at,expires_at,execution_input)
VALUES(%s,%s,'test','evolver.run_evolution',%s,NOW()-INTERVAL '2 hours',NOW()-INTERVAL '1 hour',%s::jsonb)""",
            (
                operation,
                owner,
                sha256(str(operation).encode()).hexdigest(),
                json.dumps({"view": {"chatInvocationId": str(invocation)}}),
            ),
        )


async def receipt(conn, invocation, owner, cost, source="chat"):
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO chat_usage_receipts
(call_id,invocation_id,auth_sub,step_number,call_source,usage_status,input_tokens,output_tokens,estimated_cost_usd)
VALUES(%s,%s,%s,0,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                invocation,
                owner,
                source,
                "known" if cost is not None else "unknown",
                1 if cost is not None else None,
                1 if cost is not None else None,
                cost,
            ),
        )


@pytest.mark.asyncio
async def test_chat_cost_join_uses_exact_approval_owner_and_keeps_expired_audit(database):
    args = await create_baseline(database, uuid4())
    invocation = uuid4()
    await associate(database, args, invocation)
    await receipt(database, invocation, args["requested_by_sub"], "0.0000015")
    await receipt(database, invocation, args["requested_by_sub"], None)
    await receipt(database, invocation, "other-owner", "9.99")
    await receipt(database, invocation, args["requested_by_sub"], "9.99", source="service")
    summary = await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"])
    assert summary == {
        "linked": True,
        "call_count": 2,
        "known_cost_usd": 0.0000015,
        "unknown_cost_count": 1,
        "shared_approval_count": 1,
    }
    assert await chat_costs.for_run(database, args["e1_run_id"], uuid4()) == {"linked": False}


@pytest.mark.asyncio
async def test_chat_costs_do_not_infer_missing_or_foreign_associations(database):
    args = await create_baseline(database, uuid4())
    assert await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"]) == {
        "linked": False
    }
    await associate(database, args, uuid4(), auth_sub="other-owner")
    assert await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"]) == {
        "linked": False
    }


@pytest.mark.asyncio
async def test_shared_turn_is_flagged_and_unknown_cost_is_not_zero(database):
    owner = uuid4()
    first = await create_baseline(database, owner)
    second = await create_baseline(database, owner)
    invocation = uuid4()
    await associate(database, first, invocation)
    await associate(database, second, invocation)
    await receipt(database, invocation, str(owner), None)
    for args in (first, second):
        summary = await chat_costs.for_run(database, args["e1_run_id"], owner)
        assert summary["known_cost_usd"] is None
        assert summary["unknown_cost_count"] == 1
        assert summary["shared_approval_count"] == 2


@pytest.mark.asyncio
async def test_e2_baseline_campaign_and_reused_loop_keep_original_turn(database):
    from inalpha_evolver.storage import loops

    from .test_loop_storage import create_campaign

    args = await create_baseline(database, uuid4())
    original = await loops.ensure_for_e1_run(database, **args)
    await database.execute(
        "UPDATE strategy_evo_runs SET idempotency_key=%s WHERE run_id=%s",
        (args["operation_id"], args["e1_run_id"]),
    )
    invocation = uuid4()
    await receipt(database, invocation, args["requested_by_sub"], "0.0000015")
    await database.execute(
        """INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
VALUES(%s,%s,%s,'evolver.start_evolution_loop')""",
        (args["requested_by_sub"], args["operation_id"], invocation),
    )
    later_operation, later_invocation = uuid4(), uuid4()
    await receipt(database, later_invocation, args["requested_by_sub"], "9.99")
    await database.execute(
        """INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
VALUES(%s,%s,%s,'evolver.start_evolution_loop')""",
        (args["requested_by_sub"], later_operation, later_invocation),
    )
    reused = await loops.ensure_for_e1_run(
        database, **{**args, "operation_id": str(later_operation)}
    )
    assert reused["loop_id"] == original["loop_id"]
    campaign = await create_campaign(database, args)
    await database.execute(
        "UPDATE evolution_campaigns SET requested_by_sub=%s WHERE campaign_id=%s",
        (args["requested_by_sub"], campaign),
    )
    await database.execute(
        "UPDATE evolution_loops SET campaign_id=%s WHERE loop_id=%s",
        (campaign, original["loop_id"]),
    )
    expected = {
        "linked": True,
        "call_count": 1,
        "known_cost_usd": 0.0000015,
        "unknown_cost_count": 0,
        "shared_approval_count": 1,
    }
    assert (
        await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"]) == expected
    )
    assert (
        await chat_costs.for_e2_task(
            database, original["loop_id"], args["owner_account_id"], kind="loop"
        )
        == expected
    )
    assert (
        await chat_costs.for_e2_task(database, campaign, args["owner_account_id"], kind="campaign")
        == expected
    )
    assert await chat_costs.for_e2_task(database, campaign, uuid4(), kind="campaign") == {
        "linked": False
    }
    await database.execute(
        "UPDATE strategy_evo_runs SET idempotency_key=%s WHERE run_id=%s",
        (str(uuid4()), args["e1_run_id"]),
    )
    assert await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"]) == {
        "linked": False
    }


@pytest.mark.asyncio
async def test_standalone_e2_campaign_uses_only_its_own_operation(database):
    from .test_loop_storage import create_campaign

    args = await create_baseline(database, uuid4())
    campaign = await create_campaign(database, args)
    operation, invocation = uuid4(), uuid4()
    await database.execute(
        "UPDATE evolution_campaigns SET requested_by_sub=%s,idempotency_key=%s WHERE campaign_id=%s",
        (args["requested_by_sub"], str(operation), campaign),
    )
    await receipt(database, invocation, args["requested_by_sub"], None)
    await database.execute(
        """INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
VALUES(%s,%s,%s,'evolver.run_event_campaign')""",
        (args["requested_by_sub"], operation, invocation),
    )
    summary = await chat_costs.for_e2_task(
        database, campaign, args["owner_account_id"], kind="campaign"
    )
    assert summary["linked"] and summary["known_cost_usd"] is None
    assert summary["unknown_cost_count"] == 1


@pytest.mark.asyncio
async def test_e1_cost_survives_replacement_of_expired_approval(database):
    args = await create_baseline(database, uuid4())
    invocation = uuid4()
    await associate(database, args, invocation)
    await receipt(database, invocation, args["requested_by_sub"], "0.0000015")
    await database.execute(
        """INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
SELECT auth_sub,operation_id,%s,'evolver.run_evolution' FROM evolution_approval_operations
WHERE operation_id::text=(SELECT idempotency_key FROM strategy_evo_runs WHERE run_id=%s)""",
        (invocation, args["e1_run_id"]),
    )
    before = await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"])
    await database.execute(
        """UPDATE evolution_approval_operations SET operation_id=%s,execution_input='{}'
WHERE operation_id::text=(SELECT idempotency_key FROM strategy_evo_runs WHERE run_id=%s)""",
        (uuid4(), args["e1_run_id"]),
    )
    assert await chat_costs.for_run(database, args["e1_run_id"], args["owner_account_id"]) == before
    assert before["known_cost_usd"] == 0.0000015 and before["shared_approval_count"] == 1
    assert await chat_costs.for_run(database, args["e1_run_id"], uuid4()) == {"linked": False}

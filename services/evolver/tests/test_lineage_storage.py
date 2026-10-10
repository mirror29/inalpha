"""Real graph reads preserve attempts, missing stages and owner boundaries."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from inalpha_shared.auth import User
from psycopg.errors import RaiseException

from inalpha_evolver.api.detail_routes import get_run
from inalpha_evolver.storage import candidates, lineage, loops, runs

from .test_loop_storage import create_baseline, create_campaign, loop_database  # noqa: F401
from .test_retry_storage import insert_retry


@pytest.mark.asyncio
async def test_lineage_reads_all_attempts_and_only_owned_downstream_records(database):
    args = await create_baseline(database, uuid4())
    parent = await runs.transition(
        database, args["e1_run_id"], from_statuses=("queued",), to_status="failed"
    )
    retry, _ = await insert_retry(database, parent, str(uuid4()))
    downstream = {**args, "e1_run_id": retry["run_id"], "operation_id": str(uuid4())}
    loop = await loops.ensure_for_e1_run(database, **downstream)
    campaign = await create_campaign(database, downstream)
    await database.execute(
        "UPDATE evolution_loops SET campaign_id=%s WHERE loop_id=%s", (campaign, loop["loop_id"])
    )
    other = await create_baseline(database, uuid4())
    await runs.transition(
        database, other["e1_run_id"], from_statuses=("queued",), to_status="failed"
    )
    await database.execute(
        "UPDATE strategy_evo_runs SET experiment_id=%s,attempt_number=99 WHERE run_id=%s",
        (parent["experiment_id"], other["e1_run_id"]),
    )
    foreign_campaign = await create_campaign(database, args, owner=other["owner_account_id"])
    graph = await lineage.for_run(database, retry["run_id"], args["owner_account_id"])
    assert graph["source_reference"] == "sma_cross_v1"
    assert graph["experiment_id"] == parent["experiment_id"]
    assert [item["run_id"] for item in graph["attempts"]] == [parent["run_id"], retry["run_id"]]
    assert graph["attempts"][1]["retry_of_run_id"] == parent["run_id"]
    assert [item["loop_id"] for item in graph["loops"]] == [loop["loop_id"]]
    assert [item["campaign_id"] for item in graph["campaigns"]] == [campaign]
    assert foreign_campaign not in [item["campaign_id"] for item in graph["campaigns"]]
    observation = graph["campaigns"][0]
    assert observation["locked_candidate_id"] is None
    assert observation["forward_sandbox_id"] is None
    assert observation["holdout_passed"] is None
    assert observation["adoption_count"] == 0
    assert await lineage.for_run(database, retry["run_id"], other["owner_account_id"]) is None
    foreign_graph = await lineage.for_run(database, other["e1_run_id"], other["owner_account_id"])
    assert foreign_graph["experiment_id"] is None
    assert len(foreign_graph["attempts"]) == 1
    assert (
        await lineage.for_e2_task(database, loop["loop_id"], args["owner_account_id"], kind="loop")
        == graph
    )
    assert (
        await lineage.for_e2_task(database, campaign, args["owner_account_id"], kind="campaign")
        == graph
    )
    assert "source_code" not in str(graph) and "llm_credential_grant" not in str(graph)


@pytest.mark.asyncio
async def test_standalone_campaign_does_not_invent_an_original_strategy(database):
    args = await create_baseline(database, uuid4())
    campaign = await create_campaign(database, args)
    await database.execute(
        "UPDATE evolution_campaigns SET source_run_id=NULL WHERE campaign_id=%s", (campaign,)
    )
    graph = await lineage.for_e2_task(database, campaign, args["owner_account_id"], kind="campaign")
    assert graph["source_reference"] is None and graph["experiment_id"] is None
    assert graph["attempts"] == [] and graph["loops"] == []
    assert graph["campaigns"][0]["campaign_id"] == campaign
    assert await lineage.for_e2_task(database, campaign, uuid4(), kind="campaign") is None


@pytest.mark.asyncio
async def test_recorded_stage_results_and_adoption_counts_are_owner_scoped(database):
    args = await create_baseline(database, uuid4())
    campaign = await create_campaign(database, args)
    hypothesis, champion, forward, holdout, artifact = [uuid4() for _ in range(5)]
    await database.execute(
        """INSERT INTO evolution_hypotheses
(hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,spec,spec_hash)
VALUES(%s,%s,1,0,'seed','event','{}',%s)""",
        (hypothesis, campaign, hypothesis.hex * 2),
    )
    await database.execute(
        """INSERT INTO evolution_implementations
(implementation_id,campaign_id,hypothesis_id,generation,profile,source_code,source_hash)
VALUES(%s,%s,%s,1,'direct','test-only',%s)""",
        (champion, campaign, hypothesis, champion.hex * 2),
    )
    await database.execute(
        """INSERT INTO paper_evolution_forward_sandboxes
(sandbox_id,campaign_id,candidate_id,owner_account_id,asset_id,venue,symbol,timeframe,
source_code,source_hash,hypothesis_spec,frozen_versions,event_count)
VALUES(%s,%s,%s,%s,'test-asset','test','TEST','1h','test-only',%s,'{}','{}',1)""",
        (forward, campaign, champion, args["owner_account_id"], forward.hex * 2),
    )
    await database.execute(
        """INSERT INTO evolution_holdout_attempts
(attempt_id,campaign_id,owner_account_id,candidate_id,status,passed,finished_at)
VALUES(%s,%s,%s,%s,'failed',FALSE,NOW())""",
        (holdout, campaign, args["owner_account_id"], champion),
    )
    await database.execute(
        "UPDATE evolution_campaigns SET locked_candidate_id=%s,forward_sandbox_id=%s WHERE campaign_id=%s",
        (champion, forward, campaign),
    )
    await database.execute(
        "INSERT INTO strategy_artifacts(artifact_id,source_hash,source_code) VALUES(%s,%s,'test-only')",
        (artifact, artifact.hex * 2),
    )
    await database.execute(
        """INSERT INTO strategy_adoptions(artifact_id,owner_account_id,campaign_id,evidence_grade)
VALUES(%s,%s,%s,'limited'),(%s,%s,%s,'limited')""",
        (artifact, args["owner_account_id"], campaign, artifact, uuid4(), campaign),
    )
    graph = await lineage.for_run(database, args["e1_run_id"], args["owner_account_id"])
    stage = graph["campaigns"][0]
    assert stage["locked_candidate_id"] == champion
    assert stage["forward_sandbox_id"] == forward and stage["forward_status"] == "observing"
    assert stage["forward_event_count"] == 1
    assert stage["holdout_attempt_id"] == holdout and stage["holdout_passed"] is False
    assert stage["adoption_count"] == 1
    await database.execute(
        "UPDATE paper_evolution_forward_sandboxes SET owner_account_id=%s WHERE sandbox_id=%s",
        (uuid4(), forward),
    )
    with pytest.raises(RaiseException, match="evolution holdout attempt identity is immutable"):
        async with database.transaction():
            await database.execute(
                "UPDATE evolution_holdout_attempts SET owner_account_id=%s WHERE attempt_id=%s",
                (uuid4(), holdout),
            )
    isolated = (await lineage.for_run(database, args["e1_run_id"], args["owner_account_id"]))[
        "campaigns"
    ][0]
    assert isolated["forward_sandbox_id"] is None and isolated["holdout_attempt_id"] == holdout


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["owned", "foreign", "missing", "malformed"])
async def test_evolution_candidate_source_reference_respects_run_owner(database, source_kind):
    owner = uuid4()
    source = await create_baseline(database, owner if source_kind == "owned" else uuid4())
    candidate = await candidates.insert_slot(database, source["e1_run_id"], 0, "test mutation")
    candidate_id = uuid4() if source_kind == "missing" else candidate["candidate_id"]
    reference = f"evolution_candidate:{candidate_id}"
    if source_kind == "malformed":
        reference = "evolution_candidate:not-a-uuid"
    target = await create_baseline(database, owner)
    await database.execute(
        "UPDATE strategy_evo_runs SET seed_strategy_id=%s WHERE run_id=%s",
        (reference, target["e1_run_id"]),
    )

    graph = await lineage.for_run(database, target["e1_run_id"], owner)

    assert graph["source_reference"] == (reference if source_kind == "owned" else None)
    assert graph["current_run_id"] == target["e1_run_id"]
    assert await lineage.for_run(database, target["e1_run_id"], uuid4()) is None

    response = await get_run(target["e1_run_id"], database, User(user_id=str(owner)))
    assert response.lineage["source_reference"] == graph["source_reference"]
    with pytest.raises(HTTPException) as denied:
        await get_run(target["e1_run_id"], database, User(user_id=str(uuid4())))
    assert denied.value.status_code == 404

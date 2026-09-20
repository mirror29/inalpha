"""Atomic E1-to-E2 handoff using persisted owner authority rather than a chat grant."""

from typing import Any
from uuid import UUID

from inalpha_shared.db import get_conn

from ..api.campaign_routes import _validate_event_snapshot, _validate_source_run_market
from ..api.schemas import CreateCampaignRequest, campaign_request_digest
from ..campaign_preparation import discovery_facts, prepare_campaign
from ..config import EvolverSettings
from ..data.persistent_snapshot import discovery_dataset, get_loop_data_snapshot
from ..event_client import fetch_event_snapshot
from ..hypothesis.feedback import build_loop_simulation_feedback
from ..storage import campaigns, candidates, loop_dispatch, loops, runs
from ..storage.loop_authorizations import LoopAuthorizationUnavailable


async def _authorized_loop(conn: Any, claimed: dict[str, Any]) -> dict[str, Any]:
    cursor = await conn.execute(
        """SELECT l.*,a.llm_snapshot AS authorized_llm_snapshot
FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.loop_id=%s AND l.owner_account_id=%s AND a.owner_account_id=l.owner_account_id
AND l.lease_token=%s AND l.lease_expires_at>=clock_timestamp()
AND a.revoked_at IS NULL AND a.expires_at>clock_timestamp()
AND l.status IN ('baseline_ready','campaign_running')
AND EXISTS(SELECT 1 FROM evolution_loop_steps s WHERE s.loop_id=l.loop_id AND s.step_key='baseline')
FOR UPDATE OF l,a""",
        (claimed["loop_id"], claimed["owner_account_id"], claimed["lease_token"]),
    )
    row = await cursor.fetchone()
    if row is None:
        raise LoopAuthorizationUnavailable("campaign handoff has no live baseline authority")
    return dict(row)


async def handoff_campaign(claimed: dict[str, Any], settings: EvolverSettings) -> UUID:
    """Freeze, link, and enqueue exactly one campaign; retries return that same campaign."""
    if not settings.event_evolution_enabled:
        raise LoopAuthorizationUnavailable("event evolution is disabled")
    async with get_conn() as conn:
        loop = await _authorized_loop(conn, claimed)
        if loop["campaign_id"] is not None:
            return loop["campaign_id"]
        run = await runs.get_run(conn, loop["e1_run_id"], loop["owner_account_id"])
        source_candidates = await candidates.list_candidates(
            conn, loop["e1_run_id"], loop["owner_account_id"],
        )
        frozen = await get_loop_data_snapshot(conn, loop["loop_id"], loop["owner_account_id"])
    if run is None or run["status"] != "completed" or frozen is None:
        raise LoopAuthorizationUnavailable("campaign handoff requires the completed frozen baseline")
    body = CreateCampaignRequest.model_validate({
        **loop["frozen_config"]["campaign_request"], "source_run_id": loop["e1_run_id"],
    })
    if (
        body.llm.model_dump(mode="json") != loop["authorized_llm_snapshot"]
        or body.target_kind != loop["target_kind"] or body.target_id != loop["target_id"]
    ):
        raise LoopAuthorizationUnavailable("campaign intent differs from frozen loop authority")
    _validate_source_run_market(run, body)
    feedback = build_loop_simulation_feedback(run, source_candidates, frozen)
    snapshot = await fetch_event_snapshot(
        body.event_snapshot_id, owner_account_id=loop["owner_account_id"], settings=settings,
    )
    _validate_event_snapshot(snapshot, body)
    discovery = discovery_dataset(frozen)
    safe_snapshot = {
        **snapshot, "facts": discovery_facts(snapshot, discovery.manifest.requested_as_of),
    }
    hypotheses, config = prepare_campaign(body, safe_snapshot, feedback)
    if "protection_policy" in loop["frozen_config"]:
        config["protection_policy"] = loop["frozen_config"]["protection_policy"]
    digest = campaign_request_digest(body)
    async with get_conn() as conn:
        async with conn.transaction():
            current = await _authorized_loop(conn, claimed)
            if current["campaign_id"] is not None:
                return current["campaign_id"]
            row = await campaigns.insert_campaign(
                conn, owner_account_id=loop["owner_account_id"],
                requested_by_sub=loop["requested_by_sub"],
                idempotency_key=f"loop:{loop['loop_id']}:campaign", request_hash=digest,
                source_run_id=loop["e1_run_id"], event_snapshot_id=body.event_snapshot_id,
                frozen_config=config, llm_snapshot=body.llm.model_dump(mode="json"),
                llm_credential_grant=None, hypotheses=hypotheses,
            )
            if row["request_hash"] != digest:
                raise LoopAuthorizationUnavailable("campaign handoff identity changed")
            campaign_id = row["campaign_id"]
            if not await loop_dispatch.complete_step(
                conn, loop_id=loop["loop_id"], owner_account_id=loop["owner_account_id"],
                lease_token=claimed["lease_token"], step_key="campaign", output_id=campaign_id,
            ):
                raise LoopAuthorizationUnavailable("campaign handoff lease expired")
            started = await campaigns.transition(
                conn, campaign_id, loop["owner_account_id"], from_statuses=("draft",),
                to_status="replaying", values={"data_snapshot_id": frozen["snapshot_id"], "active_generation": 1},
            )
            if started is None:
                raise LoopAuthorizationUnavailable("new campaign is not in draft state")
            await loops.sync_from_campaign(conn, loop["loop_id"], loop["owner_account_id"])
    return campaign_id

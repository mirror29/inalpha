"""E2 campaign, forward evidence, sealed holdout, and adoption API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from inalpha_paper.account_id import account_id_from_user
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ConflictError, NotFoundError, ValidationError

from ..config import get_evolver_settings
from ..event_client import fetch_event_snapshot
from ..hypothesis.compiler import compile_hypothesis, expand_implementations
from ..hypothesis.models import HypothesisSpec
from ..hypothesis.seeding import seed_generation_one
from ..runtime.campaign import evaluate_sealed_holdout
from ..sandbox.ast_audit import assert_safe
from ..storage import campaigns as store
from .approval import verify_evolution_approval
from .schemas import (
    AdoptionListResponse,
    AdoptionResponse,
    CampaignListResponse,
    CampaignResponse,
    CreateCampaignRequest,
    ForwardEvidenceRequest,
    LockChampionRequest,
    campaign_request_digest,
)

router = APIRouter()


def _response(row: dict[str, object]) -> CampaignResponse:
    return CampaignResponse(**row)


@router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    body: CreateCampaignRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    evolution_credential: Annotated[
        str,
        Header(alias="X-Evolution-Credential", min_length=100, max_length=4096),
    ],
) -> CampaignResponse:
    """Create a frozen campaign after compiling all deterministic implementation arms."""
    owner = account_id_from_user(user)
    settings = get_evolver_settings()
    if not settings.event_evolution_enabled:
        raise ValidationError(
            "event evolution is disabled",
            code="EVENT_EVOLUTION_DISABLED",
        )
    snapshot = await fetch_event_snapshot(
        body.event_snapshot_id,
        owner_account_id=owner,
        settings=settings,
    )
    hypotheses = body.hypotheses or seed_generation_one(
        snapshot,
        body.config.symbol.split("/")[0].upper(),
    )
    if len(hypotheses) != 8:
        raise ValidationError(
            "event campaign requires exactly eight generation-one hypothesis slots",
            code="CAMPAIGN_DIRECTION_COVERAGE_REQUIRED",
        )
    digest = campaign_request_digest(body)
    verify_evolution_approval(
        evolution_credential,
        owner_sub=user.user_id,
        operation_id=idempotency_key,
        config_id=body.llm.config_id,
        provider=body.llm.provider,
        llm_config_digest=body.llm.config_digest,
        request_digest=digest,
        grant_purpose="event_campaign",
        settings=settings,
    )
    for hypothesis in hypotheses:
        for implementation in expand_implementations(hypothesis):
            compiled = compile_hypothesis(implementation)
            assert_safe(compiled.source_code)
    frozen_config = {
        **body.config.model_dump(mode="json"),
        "event_snapshot": {
            "snapshot_id": snapshot["snapshot_id"],
            "events_sha256": snapshot["events_sha256"],
            "policy_version": snapshot["policy_version"],
            "fact_count": snapshot["fact_count"],
            "cutoff": snapshot["cutoff"],
        },
        "compiler_version": "event-strategy-compiler-v1",
        "selection_version": "pareto-novelty-v1",
        "fdr_q": 0.10,
        "llm_call_topology": {"calls_per_generation": 2, "hypotheses_per_call": 4},
        "estimated_reserved_llm_cost_usd": (10 * body.llm.pricing.estimated_max_usd_per_candidate),
        "sealed_holdout_thresholds": {
            "sharpe_gt": 0,
            "net_return_pct_gt": 0,
            "max_drawdown_pct_lte": 25,
            "num_trades_gt": 0,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    async with db.transaction():
        row = await store.insert_campaign(
            db,
            owner_account_id=owner,
            requested_by_sub=user.user_id,
            idempotency_key=idempotency_key,
            request_hash=digest,
            source_run_id=body.source_run_id,
            event_snapshot_id=body.event_snapshot_id,
            frozen_config=frozen_config,
            llm_snapshot=body.llm.model_dump(mode="json"),
            llm_credential_grant=evolution_credential,
            hypotheses=hypotheses,
        )
        if row.get("request_hash") != digest:
            raise ConflictError("idempotency key reused", code="IDEMPOTENCY_KEY_REUSED")
    loaded = await store.get_campaign(db, row["campaign_id"], owner)
    assert loaded is not None
    return _response(loaded)


@router.get("/campaigns", response_model=CampaignListResponse)
async def list_campaigns(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 20,
) -> CampaignListResponse:
    rows = await store.list_campaigns(db, account_id_from_user(user), limit=min(max(limit, 1), 50))
    return CampaignListResponse(items=[_response(row) for row in rows])


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    row = await store.get_campaign(db, campaign_id, account_id_from_user(user))
    if row is None:
        raise NotFoundError("campaign not found", code="CAMPAIGN_NOT_FOUND")
    return _response(row)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignResponse)
async def start_campaign(
    campaign_id: UUID,
    request: Request,
    background: BackgroundTasks,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    owner = account_id_from_user(user)
    manager = getattr(request.app.state, "campaign_manager", None)
    if manager is None:
        raise ValidationError("event evolution is disabled", code="EVENT_EVOLUTION_DISABLED")
    row = await store.transition(
        db,
        campaign_id,
        owner,
        from_statuses=("draft",),
        to_status="replaying",
        values={
            "active_generation": 1,
            "failure_code": None,
            "failure_message": None,
            "finished_at": None,
        },
    )
    if row is None:
        raise ConflictError("campaign cannot start", code="CAMPAIGN_STATE_CONFLICT")
    loaded = await store.get_campaign(db, campaign_id, owner)
    assert loaded is not None
    background.add_task(manager.notify_async)
    return _response(loaded)


@router.post("/campaigns/{campaign_id}/lock", response_model=CampaignResponse)
async def lock_campaign_champion(
    campaign_id: UUID,
    body: LockChampionRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    owner = account_id_from_user(user)
    row = await store.lock_champion(db, campaign_id, owner, body.candidate_id)
    if row is None:
        raise ConflictError(
            "campaign must finish generation five before locking one champion",
            code="CAMPAIGN_LOCK_CONFLICT",
        )
    loaded = await store.get_campaign(db, campaign_id, owner)
    assert loaded is not None
    return _response(loaded)


@router.post("/campaigns/{campaign_id}/forward", response_model=CampaignResponse)
async def record_forward_evidence(
    campaign_id: UUID,
    body: ForwardEvidenceRequest,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    owner = account_id_from_user(user)
    metrics = body.model_dump(mode="json") | {"passed": body.passed()}
    row = await store.record_forward(
        db,
        campaign_id,
        owner,
        event_count=body.event_count,
        metrics=metrics,
    )
    if row is None:
        raise ConflictError(
            "campaign is not waiting for forward evidence", code="FORWARD_STATE_CONFLICT"
        )
    loaded = await store.get_campaign(db, campaign_id, owner)
    assert loaded is not None
    return _response(loaded)


@router.post("/campaigns/{campaign_id}/holdout", response_model=CampaignResponse)
async def consume_campaign_holdout(
    campaign_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    """Run the pre-locked champion against sealed bars; request bodies cannot inject results."""
    owner = account_id_from_user(user)
    campaign = await store.get_campaign(db, campaign_id, owner)
    implementation = await store.locked_implementation(db, campaign_id, owner)
    if campaign is None or implementation is None:
        raise ConflictError(
            "sealed holdout is unavailable or already consumed",
            code="HOLDOUT_ALREADY_CONSUMED",
        )
    reserved = await store.reserve_holdout(db, campaign_id, owner)
    if reserved is None:
        raise ConflictError(
            "sealed holdout is unavailable or already consumed",
            code="HOLDOUT_ALREADY_CONSUMED",
        )
    try:
        passed, evidence = await evaluate_sealed_holdout(
            campaign,
            source_code=implementation["source_code"],
            hypothesis=HypothesisSpec.model_validate(implementation["spec"]),
            settings=get_evolver_settings(),
        )
    except Exception as exc:
        passed = False
        evidence = {
            "error_code": str(getattr(exc, "code", "SEALED_HOLDOUT_FAILED")),
            "error_message": str(exc)[:1000],
        }
    row = await store.finalize_holdout(
        db,
        campaign_id,
        owner,
        passed=passed,
        evidence=evidence,
    )
    if row is None:
        raise ConflictError(
            "sealed holdout finalization lost compare-and-swap",
            code="HOLDOUT_FINALIZE_CONFLICT",
        )
    loaded = await store.get_campaign(db, campaign_id, owner)
    assert loaded is not None
    return _response(loaded)


@router.post("/campaigns/{campaign_id}/adopt", response_model=AdoptionResponse)
async def adopt_campaign_winner(
    campaign_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> AdoptionResponse:
    owner = account_id_from_user(user)
    async with db.transaction():
        adoption = await store.adopt_graduated(db, campaign_id, owner)
    if adoption is None:
        raise ValidationError(
            "graduated campaign has no adoptable locked source",
            code="CAMPAIGN_NOT_ADOPTABLE",
        )
    return AdoptionResponse(**adoption)


@router.get("/adoptions", response_model=AdoptionListResponse)
async def list_strategy_adoptions(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 50,
) -> AdoptionListResponse:
    """List experimental campaign winners separately from promoted Paper candidates."""
    rows = await store.list_adoptions(
        db,
        account_id_from_user(user),
        limit=min(max(limit, 1), 100),
    )
    return AdoptionListResponse(items=rows)


__all__ = ["router"]

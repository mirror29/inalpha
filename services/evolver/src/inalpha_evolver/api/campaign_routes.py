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

from ..campaign_preparation import prepare_campaign
from ..config import get_evolver_settings
from ..event_client import fetch_event_snapshot
from ..hypothesis.feedback import build_source_simulation_feedback
from ..storage import campaigns as store
from ..storage import candidates, runs
from ..storage import loops as loop_store
from .approval import verify_evolution_approval
from .schemas import (
    AdoptionListResponse,
    AdoptionResponse,
    CampaignListResponse,
    CampaignResponse,
    CreateCampaignRequest,
    EvolutionCapabilitiesResponse,
    ImplementationPageResponse,
    campaign_request_digest,
)

router = APIRouter()


def _response(row: dict[str, object]) -> CampaignResponse:
    return CampaignResponse(**row)


def _require_event_evolution() -> None:
    """Fail every E2 mutation closed when the server capability is disabled."""
    if not get_evolver_settings().event_evolution_enabled:
        raise ValidationError(
            "event evolution is disabled",
            code="EVENT_EVOLUTION_DISABLED",
        )


@router.get("/capabilities", response_model=EvolutionCapabilitiesResponse)
async def evolution_capabilities(
    _user: Annotated[User, Depends(get_current_user)],
    request: Request,
) -> EvolutionCapabilitiesResponse:
    """Return the sole backend authority for whether E2 mutations are available."""
    settings = get_evolver_settings()
    enabled = settings.event_evolution_enabled
    loop_manager = getattr(request.app.state, "loop_manager", None)
    loop_enabled = bool(enabled and loop_manager is not None and loop_manager.healthy)
    return EvolutionCapabilitiesResponse(
        event_evolution_enabled=enabled,
        durable_loop_enabled=loop_enabled,
        durable_loop_reason=None if loop_enabled else "durable loop dispatcher unavailable or disabled",
        reason=None if enabled else "EVENT_EVOLUTION_ENABLED is false",
        candidate_concurrency=settings.candidate_evaluation_concurrency,
        supported_timeframes=["15m", "1h", "4h"],
    )


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
    _require_event_evolution()
    digest = campaign_request_digest(body)
    target_kind = body.target_kind or ("e1_run" if body.source_run_id else "e2_campaign")
    target_id = body.target_id or str(body.source_run_id or digest)
    active_loop = await loop_store.get_active_loop_for_target(
        db,
        owner,
        target_kind,
        target_id,
    )
    if active_loop is not None and active_loop.get("campaign_id") is not None:
        existing = await store.get_campaign(db, active_loop["campaign_id"], owner)
        if existing is not None:
            return _response(existing)
    source_feedback: dict[str, object] | None = None
    if body.source_run_id is not None:
        source_run = await runs.get_run(db, body.source_run_id, owner)
        if source_run is None:
            raise NotFoundError("source evolution run not found", code="SOURCE_RUN_NOT_FOUND")
        if source_run["status"] != "completed":
            raise ConflictError(
                "source evolution run must be completed",
                code="SOURCE_RUN_NOT_COMPLETED",
            )
        _validate_source_run_market(source_run, body)
        source_candidates = await candidates.list_candidates(db, body.source_run_id, owner)
        try:
            source_feedback = build_source_simulation_feedback(source_run, source_candidates)
        except ValueError as exc:
            raise ConflictError(
                str(exc),
                code="SOURCE_RUN_FEEDBACK_UNAVAILABLE",
            ) from exc
    snapshot = await fetch_event_snapshot(
        body.event_snapshot_id,
        owner_account_id=owner,
        settings=settings,
    )
    _validate_event_snapshot(snapshot, body)
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
    hypotheses, frozen_config = prepare_campaign(body, snapshot, source_feedback)
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
        await loop_store.ensure_for_campaign(
            db,
            owner_account_id=owner,
            requested_by_sub=user.user_id,
            operation_id=idempotency_key,
            target_kind=target_kind,
            target_id=target_id,
            target_snapshot={
                "venue": body.config.venue,
                "symbol": body.config.symbol,
                "asset_id": body.config.asset_id,
                "timeframe": body.config.timeframe,
            },
            e1_run_id=body.source_run_id,
            campaign_id=row["campaign_id"],
            frozen_config=frozen_config,
            budget={
                "max_generations": 5,
                "hypotheses_per_generation": len(hypotheses),
                "implementations_per_hypothesis": 3,
                "estimated_reserved_llm_cost_usd": frozen_config[
                    "estimated_reserved_llm_cost_usd"
                ],
            },
        )
    loaded = await store.get_campaign(db, row["campaign_id"], owner)
    assert loaded is not None
    return _response(loaded)


def _validate_source_run_market(
    source_run: dict[str, object],
    body: CreateCampaignRequest,
) -> None:
    """Reject mismatched or future feedback before it can steer proposals."""
    source_config = source_run.get("config")
    if not isinstance(source_config, dict):
        raise ConflictError(
            "source evolution run has no frozen market configuration",
            code="SOURCE_RUN_FEEDBACK_UNAVAILABLE",
        )
    source_identity = (
        str(source_config.get("venue") or "").casefold(),
        _normalized_symbol(source_config.get("symbol")),
        str(source_config.get("timeframe") or ""),
    )
    campaign_identity = (
        body.config.venue.casefold(),
        _normalized_symbol(body.config.symbol),
        body.config.timeframe,
    )
    if source_identity != campaign_identity:
        raise ValidationError(
            "source evolution run market must match the event campaign",
            code="SOURCE_RUN_MARKET_MISMATCH",
        )
    raw_source_as_of = source_config.get("as_of")
    try:
        source_as_of = datetime.fromisoformat(str(raw_source_as_of).replace("Z", "+00:00"))
        source_as_of = (
            source_as_of.replace(tzinfo=UTC)
            if source_as_of.tzinfo is None
            else source_as_of.astimezone(UTC)
        )
    except (TypeError, ValueError) as exc:
        raise ConflictError(
            "source evolution run has no valid frozen as_of",
            code="SOURCE_RUN_FEEDBACK_UNAVAILABLE",
        ) from exc
    if source_as_of > body.config.as_of:
        raise ValidationError(
            "source evolution run cannot contain feedback after the campaign as_of",
            code="SOURCE_RUN_FUTURE_FEEDBACK",
        )


def _validate_event_snapshot(
    snapshot: dict[str, object],
    body: CreateCampaignRequest,
) -> None:
    """Reject empty, future, or explicitly cross-asset event evidence."""
    fact_count = snapshot.get("fact_count")
    if not isinstance(fact_count, int) or fact_count < 1:
        raise ConflictError(
            "event snapshot contains no usable facts",
            code="EVENT_SNAPSHOT_EMPTY",
        )
    try:
        cutoff = datetime.fromisoformat(str(snapshot.get("cutoff")).replace("Z", "+00:00"))
        cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise ConflictError(
            "event snapshot has no valid cutoff",
            code="EVENT_SNAPSHOT_INVALID",
        ) from exc
    if cutoff > body.config.as_of:
        raise ValidationError(
            "event snapshot cutoff cannot exceed the campaign as_of",
            code="EVENT_SNAPSHOT_FUTURE",
        )
    raw_asset_ids = snapshot.get("asset_ids")
    snapshot_asset_ids = (
        {str(asset_id) for asset_id in raw_asset_ids}
        if isinstance(raw_asset_ids, list)
        else set()
    )
    if snapshot_asset_ids and body.config.asset_id not in snapshot_asset_ids:
        raise ValidationError(
            "event snapshot asset scope must cover the campaign symbol",
            code="EVENT_SNAPSHOT_ASSET_MISMATCH",
        )


def _normalized_symbol(value: object) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


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
    row = await store.get_campaign(
        db,
        campaign_id,
        account_id_from_user(user),
        include_source=False,
        include_implementations=False,
    )
    if row is None:
        raise NotFoundError("campaign not found", code="CAMPAIGN_NOT_FOUND")
    return _response(row)


@router.get("/campaigns/{campaign_id}/implementations", response_model=ImplementationPageResponse)
async def list_campaign_implementations(
    campaign_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 24,
    offset: int = 0,
    generation: int | None = None,
) -> ImplementationPageResponse:
    """Page candidate evidence without loading executable source or all generations."""
    owner = account_id_from_user(user)
    items, has_more = await store.list_implementations_page(
        db, campaign_id, owner_account_id=owner, limit=limit, offset=offset, generation=generation,
    )
    if await store.get_campaign(
        db,
        campaign_id,
        owner,
        include_source=False,
        include_implementations=False,
    ) is None:
        raise NotFoundError("campaign not found", code="CAMPAIGN_NOT_FOUND")
    return ImplementationPageResponse(items=items, limit=min(max(limit, 1), 100), offset=max(offset, 0), has_more=has_more)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignResponse)
async def start_campaign(
    campaign_id: UUID,
    request: Request,
    background: BackgroundTasks,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignResponse:
    _require_event_evolution()
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


@router.post("/campaigns/{campaign_id}/adopt", response_model=AdoptionResponse)
async def adopt_campaign_winner(
    campaign_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> AdoptionResponse:
    _require_event_evolution()
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

"""Owner-scoped durable EvolutionLoop read and incremental event API."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from inalpha_paper.account_id import account_id_from_user
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import NotFoundError

from ..storage import campaigns as campaign_store
from ..storage import loops as store
from .campaign_routes import create_campaign
from .schemas import (
    CreateCampaignRequest,
    EvolutionLoopEventListResponse,
    EvolutionLoopEventResponse,
    EvolutionLoopListResponse,
    EvolutionLoopResponse,
)

router = APIRouter(prefix="/evolution-loops")


@router.post("", response_model=EvolutionLoopResponse, status_code=status.HTTP_201_CREATED)
async def create_evolution_loop(
    body: CreateCampaignRequest,
    request: Request,
    background: BackgroundTasks,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
    evolution_credential: Annotated[
        str,
        Header(alias="X-Evolution-Credential", min_length=100, max_length=4096),
    ],
) -> EvolutionLoopResponse:
    """Create/reuse one target workflow and immediately hand it to the reconciler."""
    campaign = await create_campaign(
        body=body,
        db=db,
        user=user,
        idempotency_key=idempotency_key,
        evolution_credential=evolution_credential,
    )
    owner = account_id_from_user(user)
    if campaign.status == "draft":
        transitioned = await campaign_store.transition(
            db,
            campaign.campaign_id,
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
        if transitioned is None:
            refreshed = await campaign_store.get_campaign(db, campaign.campaign_id, owner)
            if refreshed is None or refreshed["status"] == "draft":
                raise RuntimeError("EvolutionLoop campaign start lost compare-and-swap")
    manager = getattr(request.app.state, "campaign_manager", None)
    if manager is not None:
        background.add_task(manager.notify_async)
    loop = await store.get_loop_by_campaign(db, campaign.campaign_id, owner)
    if loop is None:
        raise RuntimeError("EvolutionLoop projection was not created with its campaign")
    return EvolutionLoopResponse(**loop)


@router.get("", response_model=EvolutionLoopListResponse)
async def list_evolution_loops(
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    limit: int = 20,
) -> EvolutionLoopListResponse:
    rows = await store.list_loops(
        db,
        account_id_from_user(user),
        limit=min(max(limit, 1), 50),
    )
    return EvolutionLoopListResponse(items=[EvolutionLoopResponse(**row) for row in rows])


@router.get("/{loop_id}", response_model=EvolutionLoopResponse)
async def get_evolution_loop(
    loop_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
) -> EvolutionLoopResponse:
    row = await store.get_loop(db, loop_id, account_id_from_user(user))
    if row is None:
        raise NotFoundError("EvolutionLoop not found", code="EVOLUTION_LOOP_NOT_FOUND")
    return EvolutionLoopResponse(**row)


@router.get("/{loop_id}/events", response_model=None)
async def get_evolution_loop_events(
    loop_id: UUID,
    db: DBConn,
    user: Annotated[User, Depends(get_current_user)],
    after_version: int = -1,
    accept: Annotated[str | None, Header()] = None,
) -> EvolutionLoopEventListResponse | StreamingResponse:
    """Return bounded increments as JSON, or versioned SSE when requested."""
    owner = account_id_from_user(user)
    loop = await store.get_loop(db, loop_id, owner)
    if loop is None:
        raise NotFoundError("EvolutionLoop not found", code="EVOLUTION_LOOP_NOT_FOUND")
    rows = await store.list_events(
        db,
        loop_id,
        owner,
        after_version=max(-1, after_version),
    )
    items = [EvolutionLoopEventResponse(**row) for row in rows]
    if accept != "text/event-stream":
        return EvolutionLoopEventListResponse(items=items)

    async def stream():
        for item in items:
            yield (
                f"id: {item.version}\n"
                f"event: {item.event_type}\n"
                f"data: {json.dumps(item.model_dump(mode='json'), separators=(',', ':'))}\n\n"
            )
        yield f"event: heartbeat\ndata: {{\"state_version\":{loop['state_version']}}}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


__all__ = ["router"]

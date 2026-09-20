"""Internal Paper API for locked-champion Forward sandboxes."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ConflictError, NotFoundError
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_paper_settings
from ..evolution_execution_policy import EvolutionExecutionPolicy
from ..evolution_service_auth import (
    EvolutionServiceIdentity,
    evolution_service_identity,
    require_forward_create,
    require_forward_read,
)
from ..storage import evolution_forward as store

router = APIRouter(prefix="/internal/evolution-forward", tags=["evolution-forward"])


@router.get("/execution-policy", response_model=EvolutionExecutionPolicy)
async def execution_policy(
    identity: Annotated[EvolutionServiceIdentity, Depends(evolution_service_identity("evolution_execution_policy_read"))],
) -> EvolutionExecutionPolicy:
    """Expose effective protection thresholds to authorized research launches only."""
    return EvolutionExecutionPolicy.from_settings(get_paper_settings())


class CreateForwardSandboxRequest(BaseModel):
    """Frozen identity supplied by Evolver after server-side champion locking."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    candidate_id: UUID
    asset_id: str = Field(pattern=r"^asset:[A-Z0-9._:-]+$")
    venue: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=80)
    timeframe: Literal["15m", "1h", "4h"]
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_versions: dict[str, Any]


class ForwardSandboxResponse(BaseModel):
    """Source-free Paper projection consumed by Evolver."""

    sandbox_id: UUID
    campaign_id: UUID
    candidate_id: UUID
    owner_account_id: UUID
    asset_id: str
    venue: str
    symbol: str
    timeframe: str
    source_hash: str
    frozen_versions: dict[str, Any]
    status: Literal["observing", "passed", "failed", "insufficient_evidence"]
    started_at: datetime
    deadline_at: datetime
    event_count: int
    metrics: dict[str, Any] | None = None
    risk_alerts: list[Any]
    data_quality_alerts: list[Any]
    evidence_version: int
    evidence_digest: str | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def _response(row: dict[str, Any], settings: Settings) -> ForwardSandboxResponse:
    payload = dict(row)
    if payload.get("finished_at") is not None:
        signed = json.dumps(
            [
                str(payload["sandbox_id"]),
                str(payload["campaign_id"]),
                str(payload["candidate_id"]),
                payload["status"],
                int(payload["event_count"]),
                int(payload["evidence_version"]),
                payload.get("metrics"),
                payload.get("risk_alerts"),
                payload.get("data_quality_alerts"),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        payload["evidence_digest"] = hmac.new(
            settings.jwt_secret.encode(), signed, hashlib.sha256
        ).hexdigest()
    return ForwardSandboxResponse(**payload)


@router.post("/sandboxes", response_model=ForwardSandboxResponse, status_code=status.HTTP_201_CREATED)
async def create_forward_sandbox(
    body: CreateForwardSandboxRequest,
    request: Request,
    db: DBConn,
    identity: Annotated[EvolutionServiceIdentity, Depends(require_forward_create)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ForwardSandboxResponse:
    """Create idempotently only when the shared DB confirms the locked champion."""
    row = await store.create_sandbox(
        db,
        campaign_id=body.campaign_id,
        candidate_id=body.candidate_id,
        owner_account_id=identity.owner_account_id,
        asset_id=body.asset_id,
        venue=body.venue,
        symbol=body.symbol,
        timeframe=body.timeframe,
        expected_source_hash=body.source_hash,
        frozen_versions=body.frozen_versions,
    )
    if row is None:
        raise ConflictError(
            "candidate is not the campaign's locked champion",
            code="FORWARD_CHAMPION_NOT_LOCKED",
        )
    if (
        row["candidate_id"] != body.candidate_id
        or row["source_hash"] != body.source_hash
        or row["asset_id"] != body.asset_id
        or row["frozen_versions"] != body.frozen_versions
    ):
        raise ConflictError(
            "Forward sandbox idempotency identity changed",
            code="FORWARD_IDEMPOTENCY_CONFLICT",
        )
    manager = getattr(request.app.state, "evolution_forward_manager", None)
    if manager is not None:
        await manager.notify_async()
    return _response(row, settings)


@router.get("/sandboxes/{sandbox_id}", response_model=ForwardSandboxResponse)
async def get_forward_sandbox(
    sandbox_id: UUID,
    db: DBConn,
    identity: Annotated[EvolutionServiceIdentity, Depends(require_forward_read)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ForwardSandboxResponse:
    """Return Paper-computed evidence; owner JWTs are never accepted here."""
    row = await store.get_sandbox(db, sandbox_id, identity.owner_account_id)
    if row is None:
        raise NotFoundError("Forward sandbox not found", code="FORWARD_SANDBOX_NOT_FOUND")
    return _response(row, settings)


__all__ = ["router"]

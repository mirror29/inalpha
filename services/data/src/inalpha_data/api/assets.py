"""Authoritative asset identity API for internal strategy services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..asset_identity import resolve_asset_identity
from ..service_auth import DataServiceIdentity, require_asset_resolve

router = APIRouter(prefix="/assets", tags=["assets"])


class AssetIdentityResponse(BaseModel):
    """Canonical identity used across Data, Paper, Evolver and the DSL."""

    asset_id: str
    venue: str
    symbol: str
    event_asset_code: str


@router.get("/resolve", response_model=AssetIdentityResponse)
async def resolve_asset(
    venue: Annotated[str, Query(min_length=1, max_length=40)],
    symbol: Annotated[str, Query(min_length=1, max_length=80)],
    _identity: Annotated[DataServiceIdentity, Depends(require_asset_resolve)],
) -> AssetIdentityResponse:
    """Resolve a market symbol; consumers must persist the returned ``asset_id``."""
    return AssetIdentityResponse.model_validate(
        resolve_asset_identity(venue, symbol),
        from_attributes=True,
    )


__all__ = ["router"]

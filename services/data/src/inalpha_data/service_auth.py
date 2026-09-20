"""Strict short-lived service identities for the global event ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, Header
from inalpha_shared.auth import verify_jwt
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.errors import UnauthorizedError
from pydantic import BaseModel

DataTokenPurpose = Literal[
    "event_ingest",
    "event_extract",
    "event_snapshot_create",
    "event_snapshot_read",
    "event_import",
    "asset_resolve",
]


class DataServiceIdentity(BaseModel):
    """Verified caller identity accepted by internal event endpoints."""

    subject: str
    purpose: DataTokenPurpose


def service_identity_dependency(*allowed: DataTokenPurpose):
    """Build a FastAPI dependency restricted to explicit event purposes."""

    async def dependency(
        settings: Annotated[Settings, Depends(get_settings)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> DataServiceIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise UnauthorizedError("missing or malformed Authorization header")
        payload = verify_jwt(
            authorization.removeprefix("Bearer ").strip(),
            settings.jwt_secret,
            settings.jwt_algorithm,
        )
        if payload.get("token_use") != "service":
            raise UnauthorizedError("service token required", code="INVALID_TOKEN_USE")
        if payload.get("service_audience") != "data":
            raise UnauthorizedError("data service audience required", code="INVALID_AUDIENCE")
        purpose = payload.get("token_purpose")
        if purpose not in allowed:
            raise UnauthorizedError("event token purpose is not allowed", code="INVALID_TOKEN_PURPOSE")
        subject = payload.get("sub")
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        if not isinstance(subject, str) or not subject:
            raise UnauthorizedError("missing sub claim", code="INVALID_TOKEN_CLAIMS")
        if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
            raise UnauthorizedError("service token requires iat and exp", code="INVALID_TOKEN_CLAIMS")
        if expires_at - issued_at > 300 or issued_at > datetime.now(UTC).timestamp() + 30:
            raise UnauthorizedError("service token lifetime is invalid", code="INVALID_TOKEN_TTL")
        return DataServiceIdentity(subject=subject, purpose=purpose)

    return dependency


require_event_ingest = service_identity_dependency("event_ingest")
require_event_extract = service_identity_dependency("event_extract")
require_snapshot_create = service_identity_dependency("event_snapshot_create")
require_snapshot_read = service_identity_dependency("event_snapshot_read")
require_event_import = service_identity_dependency("event_import")
require_asset_resolve = service_identity_dependency("asset_resolve")


__all__ = [
    "DataServiceIdentity",
    "require_asset_resolve",
    "require_event_extract",
    "require_event_import",
    "require_event_ingest",
    "require_snapshot_create",
    "require_snapshot_read",
    "service_identity_dependency",
]

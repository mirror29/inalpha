"""Purpose-bound service JWTs for the isolated evolution Forward boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header
from inalpha_shared.auth import verify_jwt
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.errors import UnauthorizedError
from pydantic import BaseModel

ForwardTokenPurpose = Literal["evolution_forward_create", "evolution_forward_read", "evolution_execution_policy_read"]


class EvolutionServiceIdentity(BaseModel):
    """Verified Evolver identity bound to one owner account."""

    subject: str
    owner_account_id: UUID
    purpose: ForwardTokenPurpose


def evolution_service_identity(*allowed: ForwardTokenPurpose):
    """Build a strict Paper service dependency for explicit Forward operations."""

    async def dependency(
        settings: Annotated[Settings, Depends(get_settings)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> EvolutionServiceIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise UnauthorizedError("missing or malformed Authorization header")
        payload = verify_jwt(
            authorization.removeprefix("Bearer ").strip(),
            settings.jwt_secret,
            settings.jwt_algorithm,
        )
        if payload.get("token_use") != "service":
            raise UnauthorizedError("service token required", code="INVALID_TOKEN_USE")
        if payload.get("service_audience") != "paper":
            raise UnauthorizedError("paper service audience required", code="INVALID_AUDIENCE")
        purpose = payload.get("token_purpose")
        if purpose not in allowed:
            raise UnauthorizedError("Forward token purpose is not allowed", code="INVALID_TOKEN_PURPOSE")
        issued_at, expires_at = payload.get("iat"), payload.get("exp")
        if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
            raise UnauthorizedError("service token requires iat and exp", code="INVALID_TOKEN_CLAIMS")
        if expires_at - issued_at > 300 or issued_at > datetime.now(UTC).timestamp() + 30:
            raise UnauthorizedError("service token lifetime is invalid", code="INVALID_TOKEN_TTL")
        try:
            owner_account_id = UUID(str(payload["owner_account_id"]))
        except (KeyError, ValueError) as exc:
            raise UnauthorizedError("invalid owner account claim", code="INVALID_TOKEN_CLAIMS") from exc
        return EvolutionServiceIdentity(
            subject=str(payload.get("sub") or ""),
            owner_account_id=owner_account_id,
            purpose=purpose,
        )

    return dependency


require_forward_create = evolution_service_identity("evolution_forward_create")
require_forward_read = evolution_service_identity("evolution_forward_read")

__all__ = [
    "EvolutionServiceIdentity",
    "evolution_service_identity",
    "require_forward_create",
    "require_forward_read",
]

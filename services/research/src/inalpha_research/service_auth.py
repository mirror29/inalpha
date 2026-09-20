"""Purpose-bound service authentication for tenant-neutral extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header
from inalpha_shared.auth import verify_jwt
from inalpha_shared.errors import UnauthorizedError

from .config import ResearchSettings, get_research_settings


async def require_event_extract_request(
    settings: Annotated[ResearchSettings, Depends(get_research_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Accept only short-lived platform requests addressed to Research."""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("missing or malformed Authorization header")
    payload = verify_jwt(
        authorization.removeprefix("Bearer ").strip(),
        settings.jwt_secret,
        settings.jwt_algorithm,
    )
    if payload.get("token_use") != "service":
        raise UnauthorizedError("service token required", code="INVALID_TOKEN_USE")
    if payload.get("service_audience") != "research":
        raise UnauthorizedError("research service audience required", code="INVALID_AUDIENCE")
    if payload.get("token_purpose") != "event_extract_request":
        raise UnauthorizedError("event extraction purpose required", code="INVALID_TOKEN_PURPOSE")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    subject = payload.get("sub")
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(issued_at, (int, float))
        or not isinstance(expires_at, (int, float))
    ):
        raise UnauthorizedError("invalid service claims", code="INVALID_TOKEN_CLAIMS")
    if expires_at - issued_at > 300 or issued_at > datetime.now(UTC).timestamp() + 30:
        raise UnauthorizedError("service token lifetime is invalid", code="INVALID_TOKEN_TTL")
    return subject


__all__ = ["require_event_extract_request"]

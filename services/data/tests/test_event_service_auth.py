"""Internal event endpoints require purpose-bound short-lived service JWTs."""

from __future__ import annotations

import time

import jwt
import pytest
from inalpha_shared.config import Settings
from inalpha_shared.errors import UnauthorizedError

from inalpha_data.service_auth import service_identity_dependency

_SECRET = "event-service-auth-test-secret-with-enough-entropy"


def _settings() -> Settings:
    return Settings(DATABASE_URL="postgresql://unused", JWT_SECRET=_SECRET)


def _token(**updates: object) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "service:research",
        "token_use": "service",
        "service_audience": "data",
        "token_purpose": "event_extract",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(updates)
    return jwt.encode(payload, _SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_event_service_identity_accepts_only_the_declared_purpose() -> None:
    dependency = service_identity_dependency("event_extract")
    identity = await dependency(
        settings=_settings(),
        authorization=f"Bearer {_token()}",
    )
    assert identity.subject == "service:research"
    assert identity.purpose == "event_extract"

    with pytest.raises(UnauthorizedError) as exc_info:
        await dependency(
            settings=_settings(),
            authorization=f"Bearer {_token(token_purpose='event_ingest')}",
        )
    assert exc_info.value.code == "INVALID_TOKEN_PURPOSE"


@pytest.mark.asyncio
async def test_event_service_identity_rejects_owner_and_overlong_tokens() -> None:
    dependency = service_identity_dependency("event_extract")
    with pytest.raises(UnauthorizedError) as owner_error:
        await dependency(
            settings=_settings(),
            authorization=f"Bearer {_token(token_use='owner')}",
        )
    assert owner_error.value.code == "INVALID_TOKEN_USE"

    now = int(time.time())
    with pytest.raises(UnauthorizedError) as ttl_error:
        await dependency(
            settings=_settings(),
            authorization=f"Bearer {_token(iat=now, exp=now + 301)}",
        )
    assert ttl_error.value.code == "INVALID_TOKEN_TTL"

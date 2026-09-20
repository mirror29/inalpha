"""Paper Forward boundary rejects owner and incorrectly purposed JWTs."""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest
from inalpha_shared.config import Settings
from inalpha_shared.errors import UnauthorizedError

from inalpha_paper.evolution_service_auth import evolution_service_identity

_SECRET = "paper-forward-service-auth-secret-with-enough-entropy"


def _token(**updates: object) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": "service:evolver",
        "token_use": "service",
        "service_audience": "paper",
        "token_purpose": "evolution_forward_create",
        "owner_account_id": str(uuid4()),
        "iat": now,
        "exp": now + 300,
    }
    payload.update(updates)
    return jwt.encode(payload, _SECRET, algorithm="HS256")


@pytest.mark.asyncio
async def test_forward_identity_requires_service_audience_purpose_and_owner() -> None:
    dependency = evolution_service_identity("evolution_forward_create")
    settings = Settings(DATABASE_URL="postgresql://unused", JWT_SECRET=_SECRET)

    identity = await dependency(settings=settings, authorization=f"Bearer {_token()}")
    assert identity.purpose == "evolution_forward_create"

    for updates, code in [
        ({"token_use": "owner"}, "INVALID_TOKEN_USE"),
        ({"service_audience": "data"}, "INVALID_AUDIENCE"),
        ({"token_purpose": "evolution_forward_read"}, "INVALID_TOKEN_PURPOSE"),
        ({"owner_account_id": "not-a-uuid"}, "INVALID_TOKEN_CLAIMS"),
    ]:
        with pytest.raises(UnauthorizedError) as exc_info:
            await dependency(
                settings=settings,
                authorization=f"Bearer {_token(**updates)}",
            )
        assert exc_info.value.code == code

"""Short-lived JWTs minted by Research for internal downstream calls."""

from __future__ import annotations

import time

import jwt

from .config import ResearchSettings


def mint_data_event_token(settings: ResearchSettings) -> str:
    """Mint one five-minute token restricted to Data event extraction."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "service:research",
            "token_use": "service",
            "service_audience": "data",
            "token_purpose": "event_extract",
            "iat": now,
            "exp": now + 300,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


__all__ = ["mint_data_event_token"]

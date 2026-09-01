"""Data event snapshot client with short-lived owner-bound service JWT."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx
import jwt
from inalpha_shared.errors import InalphaError, NotFoundError

from .config import EvolverSettings


async def fetch_event_snapshot(
    snapshot_id: UUID,
    *,
    owner_account_id: UUID,
    settings: EvolverSettings,
) -> dict[str, Any]:
    """Resolve and freeze Data-owned snapshot metadata without direct table access."""
    token = jwt.encode(
        {
            "sub": str(owner_account_id),
            "token_use": "service",
            "service_audience": "data",
            "exp": int(time.time()) + min(settings.service_token_ttl_s, 300),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    url = f"{settings.data_service_url.rstrip('/')}/events/snapshots/{snapshot_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as exc:
        raise InalphaError(
            "event snapshot data service is unreachable",
            code="EVENT_DATA_UNREACHABLE",
            status_code=502,
        ) from exc
    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        payload_detail = detail.get("detail", detail) if isinstance(detail, dict) else {}
        code = (
            str(payload_detail.get("code"))
            if isinstance(payload_detail, dict) and payload_detail.get("code")
            else "EVENT_SNAPSHOT_UNAVAILABLE"
        )
        message = (
            str(payload_detail.get("message"))
            if isinstance(payload_detail, dict) and payload_detail.get("message")
            else f"event snapshot unavailable: HTTP {response.status_code}"
        )
        if response.status_code == 404:
            raise NotFoundError(message, code=code)
        raise InalphaError(
            message,
            code=code,
            status_code=response.status_code if response.status_code in {401, 403, 503} else 502,
        )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("snapshot_id") != str(snapshot_id):
        raise InalphaError(
            "event snapshot response identity mismatch",
            code="EVENT_SNAPSHOT_IDENTITY_MISMATCH",
            status_code=502,
        )
    return payload


__all__ = ["fetch_event_snapshot"]

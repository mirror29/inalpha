"""Idempotent Evolver-to-Paper handoff for locked Forward champions."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
from inalpha_paper.evolution_execution_policy import EvolutionExecutionPolicy

from .config import EvolverSettings


async def fetch_execution_policy(owner_account_id: Any, settings: EvolverSettings) -> dict[str, Any]:
    """Read and validate Paper's current policy for a single immutable launch snapshot."""
    now = int(time.time())
    token = jwt.encode({
        "sub": "service:evolver", "token_use": "service", "service_audience": "paper",
        "token_purpose": "evolution_execution_policy_read", "owner_account_id": str(owner_account_id),
        "iat": now, "exp": now + min(settings.service_token_ttl_s, 300),
    }, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    async with httpx.AsyncClient(
        base_url=settings.paper_service_url, timeout=settings.evolver_data_timeout_s,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.get("/internal/evolution-forward/execution-policy")
    response.raise_for_status()
    return EvolutionExecutionPolicy.model_validate(response.json()).model_dump(mode="json")


async def create_forward_sandbox(
    campaign: dict[str, Any],
    settings: EvolverSettings,
) -> dict[str, Any]:
    """Ask Paper to freeze the already locked implementation in an isolated sandbox."""
    candidate_id = campaign.get("locked_candidate_id")
    implementation = next(
        (
            item
            for item in campaign.get("implementations", [])
            if item.get("implementation_id") == candidate_id
        ),
        None,
    )
    if implementation is None:
        raise RuntimeError("locked campaign candidate is missing from the read model")
    frozen = campaign["frozen_config"]
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "service:evolver",
            "token_use": "service",
            "service_audience": "paper",
            "token_purpose": "evolution_forward_create",
            "owner_account_id": str(campaign["owner_account_id"]),
            "iat": now,
            "exp": now + min(settings.service_token_ttl_s, 300),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    body = {
        "campaign_id": str(campaign["campaign_id"]),
        "candidate_id": str(candidate_id),
        "asset_id": frozen["asset_id"],
        "venue": frozen["venue"],
        "symbol": frozen["symbol"],
        "timeframe": frozen["timeframe"],
        "source_hash": implementation["source_hash"],
        "frozen_versions": {
            **({"protection_policy": frozen["protection_policy"]} if "protection_policy" in frozen else {}),
            "data_snapshot_id": str(campaign.get("data_snapshot_id") or ""),
            "event_snapshot_id": str(campaign["event_snapshot_id"]),
            "compiler_version": frozen["compiler_version"],
            "execution_model_version": frozen["execution_model_version"],
            "control_matcher_version": frozen["control_matcher_version"],
            "fee_rate": frozen["fee_rate"],
            "funding_rate": frozen.get("funding_rate", 0),
            "initial_cash": frozen["initial_cash"],
            "trading_mode": frozen["trading_mode"],
            "leverage": frozen["leverage"],
            "event_policy_version": frozen["event_snapshot"]["policy_version"],
            "random_seed": frozen["random_seed"],
        },
    }
    async with httpx.AsyncClient(
        base_url=settings.paper_service_url,
        timeout=settings.evolver_data_timeout_s,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.post("/internal/evolution-forward/sandboxes", json=body)
    response.raise_for_status()
    return dict(response.json())


async def get_forward_sandbox(
    campaign: dict[str, Any],
    settings: EvolverSettings,
) -> dict[str, Any]:
    """Read and authenticate Paper-computed Forward evidence for one sandbox."""
    sandbox_id = campaign.get("forward_sandbox_id")
    if sandbox_id is None:
        raise RuntimeError("waiting Forward campaign has no Paper sandbox reference")
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "service:evolver",
            "token_use": "service",
            "service_audience": "paper",
            "token_purpose": "evolution_forward_read",
            "owner_account_id": str(campaign["owner_account_id"]),
            "iat": now,
            "exp": now + min(settings.service_token_ttl_s, 300),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    async with httpx.AsyncClient(
        base_url=settings.paper_service_url,
        timeout=settings.evolver_data_timeout_s,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.get(f"/internal/evolution-forward/sandboxes/{sandbox_id}")
    response.raise_for_status()
    payload = dict(response.json())
    terminal = payload.get("status") in {"passed", "failed", "insufficient_evidence"}
    if terminal and payload.get("finished_at") is None:
        raise RuntimeError("Paper Forward terminal evidence is missing its finish time")
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
        expected = hmac.new(
            settings.jwt_secret.encode(), signed, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(str(payload.get("evidence_digest") or ""), expected):
            raise RuntimeError("Paper Forward evidence signature is invalid")
    return payload


__all__ = ["create_forward_sandbox", "get_forward_sandbox"]

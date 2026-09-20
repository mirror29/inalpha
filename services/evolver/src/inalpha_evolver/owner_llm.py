"""按 run 冻结快照解析 owner 的既有加密 LLM 凭据。"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from inalpha_shared_llm import LLMClient  # type: ignore[import-untyped]
from inalpha_shared_llm.config import LLMSettings  # type: ignore[import-untyped]

from .config import EvolverSettings
from .loop_llm import BudgetedLoopClient, LoopModelScope
from .mutator import Mutator
from .storage.loop_authorizations import LoopAuthorizationUnavailable

_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
}


class CredentialTemporarilyUnavailable(RuntimeError):
    """Dashboard 暂时不可达；run 应回到队列而不是进入失败终态。"""

    code = "EVOLUTION_CREDENTIAL_UNAVAILABLE"


async def build_owner_mutator(
    run: dict[str, Any],
    settings: EvolverSettings,
    *,
    loop_scope: LoopModelScope | None = None,
) -> Mutator:
    """只把明文 key 留在当前进程内；run/日志均仅保存 config reference。"""
    snapshot = run.get("llm_snapshot")
    if not isinstance(snapshot, dict):
        raise RuntimeError("run is missing frozen LLM snapshot")
    config_id = str(snapshot["config_id"])
    token = run.get("llm_credential_grant")
    if loop_scope is not None:
        if str(run.get("owner_account_id")) != str(loop_scope.owner_account_id):
            raise LoopAuthorizationUnavailable("model owner does not match loop scope")
        token = await _renew_loop_grant(loop_scope, settings)
    if not isinstance(token, str) or not token:
        raise RuntimeError("run is missing its approved credential grant")
    url = (
        f"{settings.dashboard_service_url.rstrip('/')}/api/internal/llm-config/"
        f"{quote(config_id, safe='')}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=settings.evolver_credential_timeout_s,
            trust_env=False,
        ) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CredentialTemporarilyUnavailable(
            f"owner LLM credential service unavailable: {type(exc).__name__}"
        ) from exc
    if response.status_code >= 500:
        raise CredentialTemporarilyUnavailable(
            f"owner LLM credential service unavailable: HTTP {response.status_code}"
        )
    if response.status_code != 200:
        raise RuntimeError(f"owner LLM credential unavailable: HTTP {response.status_code}")
    credential = response.json()
    if (
        credential.get("config_id") != config_id
        or credential.get("provider") != snapshot["provider"]
    ):
        raise RuntimeError("owner LLM credential no longer matches frozen snapshot")
    api_key = credential.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        raise RuntimeError("owner LLM credential response omitted api_key")
    official_base_url = _BASE_URLS[snapshot["provider"]]
    snapshot_base_url = snapshot.get("base_url")
    if snapshot["provider"] == "deepseek" and snapshot_base_url == f"{official_base_url}/v1":
        snapshot_base_url = official_base_url
    if snapshot_base_url != official_base_url:
        raise RuntimeError("frozen LLM snapshot must use the official provider endpoint")
    pricing = snapshot["pricing"]
    llm_settings = LLMSettings(
        LLM_API_KEY=api_key,
        DEEPSEEK_API_KEY="",
        LLM_BASE_URL=official_base_url,
        LLM_MODEL=snapshot["model"],
        LLM_TIMEOUT_S=settings.evolver_llm_timeout_s,
        LLM_MAX_TOKENS=int(pricing["max_output_tokens"]),
    )
    llm_client = LLMClient(settings=llm_settings)
    if loop_scope is not None:
        sdk = await llm_client._ensure_client()
        # One reservation covers one network attempt, not invisible SDK retries.
        sdk.max_retries = 0
    return Mutator(
        llm_client=(BudgetedLoopClient(llm_client, loop_scope, pricing)
                    if loop_scope is not None else llm_client),
        input_usd_per_million=float(pricing["input_usd_per_million"]),
        output_usd_per_million=float(pricing["output_usd_per_million"]),
        max_input_utf8_bytes=int(pricing["assumed_input_tokens"]),
        max_output_tokens=int(pricing["max_output_tokens"]),
    )


async def _renew_loop_grant(scope: LoopModelScope, settings: EvolverSettings) -> str:
    """Ask the signer for a short-lived grant bound to the current persisted lease."""
    now = int(time.time())
    token = jwt.encode({
        "sub": "service:evolver", "aud": "inalpha-orchestration",
        "token_use": "service", "token_purpose": "evolution_loop_credential",
        "loop_id": str(scope.loop_id), "owner_account_id": str(scope.owner_account_id),
        "lease_token": str(scope.lease_token), "phase": scope.phase,
        "iat": now, "exp": now + 120,
    }, settings.jwt_secret, algorithm="HS256")
    url = (
        f"{settings.orchestration_service_url.rstrip('/')}"
        f"/internal/evolution-loops/{scope.loop_id}/credential-grant"
    )
    try:
        async with httpx.AsyncClient(
            timeout=settings.evolver_credential_timeout_s, trust_env=False,
        ) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise CredentialTemporarilyUnavailable("loop credential signer unavailable") from exc
    if response.status_code in (408, 429) or response.status_code >= 500:
        raise CredentialTemporarilyUnavailable(
            f"loop credential signer unavailable: HTTP {response.status_code}"
        )
    if response.status_code != 200:
        raise LoopAuthorizationUnavailable("loop credential signer rejected worker authority")
    grant = response.json().get("credential_grant")
    if not isinstance(grant, str) or not grant:
        raise CredentialTemporarilyUnavailable("loop credential signer omitted grant")
    return grant


__all__ = ["CredentialTemporarilyUnavailable", "build_owner_mutator"]

"""Wire the #206 runner to frozen evidence and the unified usage ledger."""

from __future__ import annotations

from typing import Any

from inalpha_shared.usage import UsageIdentity, UsageRecorder

from ..llm.client import LLMClient, build_llm_client
from .artifacts import PilotArtifacts


def pilot_client(
    artifacts: PilotArtifacts,
    *,
    auth_sub: str,
    store: Any,
    provider: str,
    model: str,
    api_key: str,
    pricing: dict[str, Any] | None = None,
) -> LLMClient:
    """Credentials stay in memory; #206 owns arm prompts and budgets, not this adapter."""
    recorder = UsageRecorder(
        UsageIdentity(
            auth_sub=auth_sub,
            service="research",
            operation_id=artifacts.run_id,
            provider=provider,
            model=model,
            stage="pilot",
            links=artifacts.links,
            pricing=pricing,
        ),
        store,
        family=provider,
        event_sink=artifacts.event,
    )
    return build_llm_client(provider=provider, model=model, api_key=api_key, recorder=recorder)

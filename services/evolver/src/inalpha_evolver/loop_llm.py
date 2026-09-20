"""Fenced, bounded model access for durable research; no model keys are persisted."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from inalpha_shared.db import get_conn

from .storage import loop_authorizations as authority


@dataclass(frozen=True, slots=True)
class LoopModelScope:
    """The worker lease checked again before every billable provider request."""

    loop_id: UUID
    owner_account_id: UUID
    lease_token: UUID
    phase: Literal["baseline", "campaign"]


async def campaign_model_scope(campaign: dict[str, Any]) -> LoopModelScope | None:
    """Resolve durable authority from owned storage, never from a client-supplied config tag."""
    async with get_conn() as conn:
        cursor = await conn.execute(
            """SELECT l.loop_id,a.llm_snapshot FROM evolution_loops l
JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.campaign_id=%s AND l.owner_account_id=%s AND a.owner_account_id=l.owner_account_id""",
            (campaign["campaign_id"], campaign["owner_account_id"]),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    if campaign["llm_snapshot"] != row["llm_snapshot"]:
        raise authority.LoopAuthorizationUnavailable("campaign model differs from loop authorization")
    return LoopModelScope(
        row["loop_id"], campaign["owner_account_id"], UUID(str(campaign["lease_token"])), "campaign",
    )


class BudgetedLoopClient:
    """Reserve each network attempt, retaining its maximum cost if usage is unknown."""

    def __init__(self, client: Any, scope: LoopModelScope, pricing: dict[str, Any]) -> None:
        self._client = client
        self._scope = scope
        self._pricing = pricing

    async def mutate(self, request: Any) -> Any:
        """A rejected budget or lease must fail before any request reaches the provider."""
        pricing = self._pricing
        if (
            len(request.system_prompt.encode()) + len(request.user_prompt.encode())
            > int(pricing["assumed_input_tokens"])
            or not 0 < request.max_tokens <= int(pricing["max_output_tokens"])
        ):
            raise authority.LoopAuthorizationUnavailable("model request exceeds frozen token limits")
        reservation = uuid4()
        scope = asdict(self._scope)
        maximum = Decimal(str(pricing["estimated_max_usd_per_candidate"]))
        async with get_conn() as conn:
            await authority.reserve(
                conn, **scope, reservation_id=reservation,
                step_key=f"{self._scope.phase}:model", amount_usd=maximum,
            )
        response = await self._client.mutate(request)
        metrics = response.cache_metrics
        # Zero usage can mean a provider omitted its receipt; it is not proof of a free call.
        if metrics.input_tokens > 0 and metrics.output_tokens >= 0:
            actual = (
                Decimal(metrics.input_tokens) * Decimal(str(pricing["input_usd_per_million"]))
                + Decimal(metrics.output_tokens) * Decimal(str(pricing["output_usd_per_million"]))
            ) / Decimal(1_000_000)
            if actual > maximum:
                raise authority.LoopAuthorizationUnavailable("provider receipt exceeds frozen maximum")
            async with get_conn() as conn:
                await authority.settle(
                    conn, **scope, reservation_id=reservation, actual_usd=actual,
                )
        return response

    async def close(self) -> None:
        """Dispose the in-memory provider credential with the underlying client."""
        await self._client.close()

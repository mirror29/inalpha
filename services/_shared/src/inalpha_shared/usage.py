"""Provider-neutral request accounting. Business context belongs to the caller."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)
_stage: ContextVar[str | None] = ContextVar("llm_usage_stage", default=None)


@contextmanager
def usage_stage(stage: str) -> Iterator[None]:
    """Scope a stage to this async task, including its nested calls."""
    token = _stage.set(stage)
    try:
        yield
    finally:
        _stage.reset(token)


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize_usage(response: Any, family: str) -> dict[str, Any]:
    """Normalize totals; cache and reasoning fields are subsets, never extra totals."""
    usage = _get(response, "usage_metadata" if family == "gemini" else "usage")
    cache = writes = reasoning = None
    if family == "anthropic":
        inp = _count(_get(usage, "input_tokens"))
        out = _count(_get(usage, "output_tokens"))
        cache = _count(_get(usage, "cache_read_input_tokens"))
        writes = _count(_get(usage, "cache_creation_input_tokens"))
        if inp is not None:
            inp += (cache or 0) + (writes or 0)
    elif family == "gemini":
        inp = _count(_get(usage, "prompt_token_count"))
        out = _count(_get(usage, "candidates_token_count"))
        reasoning = _count(_get(usage, "thoughts_token_count"))
        cache = _count(_get(usage, "cached_content_token_count"))
        if out is not None:
            out += reasoning or 0
    else:
        inp = _count(_get(usage, "prompt_tokens"))
        out = _count(_get(usage, "completion_tokens"))
        cache = _count(_get(_get(usage, "prompt_tokens_details"), "cached_tokens"))
        if cache is None:
            cache = _count(_get(usage, "prompt_cache_hit_tokens"))
        reasoning = _count(_get(_get(usage, "completion_tokens_details"), "reasoning_tokens"))
    if cache is not None and inp is not None and cache > inp:
        cache = None
    if reasoning is not None and out is not None and reasoning > out:
        reasoning = None
    return dict(
        usage_status="known" if inp is not None and out is not None else "unknown",
        input_tokens=inp,
        output_tokens=out,
        cached_input_tokens=cache,
        cache_write_tokens=writes,
        reasoning_tokens=reasoning,
    )


def estimate_cost(usage: dict[str, Any], pricing: dict[str, Any] | None) -> Decimal | None:
    """Use a frozen USD non-cache estimate; never imply this is an invoice."""
    if (
        usage["usage_status"] != "known"
        or not pricing
        or pricing.get("currency") != "USD"
        or not pricing.get("version")
    ):
        return None
    try:
        rates = [
            Decimal(str(pricing[k])) for k in ("input_usd_per_million", "output_usd_per_million")
        ]
        if any(not r.is_finite() or r < 0 for r in rates):
            return None
        return (usage["input_tokens"] * rates[0] + usage["output_tokens"] * rates[1]) / Decimal(
            1_000_000
        )
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class UsageIdentity:
    auth_sub: str
    service: str
    operation_id: str
    provider: str
    model: str
    stage: str = "model"
    config_id: str | None = None
    parent_operation_id: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    pricing: dict[str, Any] | None = None


class PostgresUsageStore:
    """Short independent transactions survive cancellation of the business transaction."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    async def owned_operation(self, auth_sub: str, operation_id: str) -> str | None:
        """Resolve attribution without allowing cross-owner parent links."""
        async with await AsyncConnection.connect(
            self.database_url, connect_timeout=5, options="-c statement_timeout=5000"
        ) as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM llm_usage_calls WHERE auth_sub=%s AND operation_id=%s LIMIT 1",
                (auth_sub, operation_id),
            )
            return operation_id if await cursor.fetchone() else None

    async def begin(
        self,
        identity: UsageIdentity,
        call_id: str,
        logical_id: str,
        attempt: int,
        sampling: dict[str, Any],
        stage: str,
    ) -> None:
        async with await AsyncConnection.connect(
            self.database_url, connect_timeout=5, options="-c statement_timeout=5000"
        ) as conn:
            await conn.execute(
                """INSERT INTO llm_usage_calls
(call_id,logical_call_id,auth_sub,service,stage,operation_id,parent_operation_id,attempt,
 provider,model,config_id,pricing,links,sampling)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    call_id,
                    logical_id,
                    identity.auth_sub,
                    identity.service,
                    stage,
                    identity.operation_id,
                    identity.parent_operation_id,
                    attempt,
                    identity.provider,
                    identity.model,
                    identity.config_id,
                    Jsonb(identity.pricing) if identity.pricing else None,
                    Jsonb(identity.links),
                    Jsonb(sampling),
                ),
            )

    async def settle(self, identity: UsageIdentity, call_id: str, receipt: dict[str, Any]) -> None:
        async with await AsyncConnection.connect(
            self.database_url, connect_timeout=5, options="-c statement_timeout=5000"
        ) as conn:
            await conn.execute(
                """UPDATE llm_usage_calls SET status=%s,usage_status=%s,
input_tokens=%s,output_tokens=%s,cached_input_tokens=%s,cache_write_tokens=%s,reasoning_tokens=%s,
estimated_cost_usd=%s,response_model=%s,finish_reason=%s,latency_ms=%s,settled_at=NOW()
WHERE call_id=%s AND auth_sub=%s AND settled_at IS NULL""",
                (
                    *(
                        receipt[k]
                        for k in (
                            "status",
                            "usage_status",
                            "input_tokens",
                            "output_tokens",
                            "cached_input_tokens",
                            "cache_write_tokens",
                            "reasoning_tokens",
                            "estimated_cost_usd",
                            "response_model",
                            "finish_reason",
                            "latency_ms",
                        )
                    ),
                    call_id,
                    identity.auth_sub,
                ),
            )


class UsageRecorder:
    """Observe one actual SDK attempt without logging exceptions, credentials or content."""

    def __init__(
        self, identity: UsageIdentity, store: Any, family: str = "openai", event_sink: Any = None
    ):
        if not identity.auth_sub.strip():
            raise ValueError("usage requires authenticated owner")
        self.identity, self.store, self.family = identity, store, family
        self.event_sink = event_sink

    async def request(
        self,
        invoke: Callable[[], Awaitable[Any]],
        *,
        logical_id: str | None = None,
        attempt: int = 0,
        sampling: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
        validate: Any = None,
    ) -> Any:
        call_id = str(uuid4())
        await self.store.begin(
            self.identity,
            call_id,
            logical_id or call_id,
            attempt,
            sampling or {},
            _stage.get() or self.identity.stage,
        )
        if self.event_sink:
            self.event_sink(
                {
                    "event": "started",
                    "call_id": call_id,
                    "logical_call_id": logical_id or call_id,
                    "attempt": attempt,
                    "stage": _stage.get() or self.identity.stage,
                    "sampling": sampling or {},
                    "request": artifact,
                }
            )
        started = time.monotonic()
        response = None
        status = "completed"
        try:
            response = await invoke()
            if validate is not None:
                try:
                    validate(response)
                except Exception:
                    status = "invalid"
                    raise
            return response
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:
            if status != "invalid":
                status = "failed"
            raise
        finally:
            receipt = normalize_usage(response, self.family)
            choices = _get(response, "choices", []) or []
            candidates = _get(response, "candidates", []) or []
            finish = (
                _get(choices[0], "finish_reason")
                if choices
                else _get(response, "stop_reason")
                or (_get(candidates[0], "finish_reason") if candidates else None)
            )
            finish = str(finish)[:100] if finish is not None else None
            if finish in ("length", "max_tokens", "MAX_TOKENS", "FinishReason.MAX_TOKENS"):
                status = "truncated"
            receipt.update(
                status=status,
                finish_reason=finish,
                response_model=_get(response, "model") or _get(response, "model_version"),
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                estimated_cost_usd=estimate_cost(receipt, self.identity.pricing),
            )
            ledger_settled = False
            for retry in range(3):
                try:
                    await self.store.settle(self.identity, call_id, receipt)
                    ledger_settled = True
                    break
                except Exception:
                    if retry == 2:
                        logger.error(
                            "usage settlement unavailable; call remains pending: %s", call_id
                        )
                    else:
                        await asyncio.sleep(0.05 * (retry + 1))
            if self.event_sink:
                text = (
                    _get(_get(choices[0], "message"), "content")
                    if choices
                    else _get(response, "text")
                )
                if text is None and self.family == "anthropic":
                    text = [_get(block, "text") for block in (_get(response, "content", []) or [])]
                self.event_sink(
                    {
                        "event": "settled",
                        "ledger_settled": ledger_settled,
                        "call_id": call_id,
                        "status": status,
                        "usage": normalize_usage(response, self.family),
                        "output": text,
                    }
                )

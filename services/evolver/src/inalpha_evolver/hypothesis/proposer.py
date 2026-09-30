"""Owner-scoped Agent proposer for structured hypothesis DSL only."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from inalpha_shared_llm.types import MutationRequest  # type: ignore[import-untyped]
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from ..exceptions import LoopControlError
from ..mutator import Mutator
from .models import _EVENT_TYPES, HypothesisSpec

_SYSTEM_PROMPT = """You are Inalpha's crypto strategy-hypothesis proposer.
Return only a JSON array of exactly four objects. Propose falsifiable event reaction mechanisms,
not prose strategies and never executable code. You may use only supplied frozen evidence IDs and
aggregate simulation feedback. Never infer publication time, unseen news, or future outcomes.
Each object may set: thesis,event_types,applicable_regimes,direction,trigger_mode,
confirmation,invalidation,risk,counterfactual. Preserve diversity and avoid semantic duplicates."""

_ALLOWED_FIELDS = {
    "thesis",
    "event_types",
    "applicable_regimes",
    "direction",
    "trigger_mode",
    "confirmation",
    "invalidation",
    "risk",
    "counterfactual",
}


class _PromptBudgetExceeded(ValueError):
    """Reject a prompt locally before any provider call."""


@dataclass(frozen=True, slots=True)
class ProposalResult:
    """Two-call proposal output plus measured provider cost."""

    hypotheses: tuple[HypothesisSpec, ...]
    cost_usd: float
    fallback_calls: int
    diagnostics: tuple[dict[str, Any], ...] = ()


async def propose_generation(
    mutator: Mutator,
    *,
    generation: int,
    scaffolds: list[HypothesisSpec],
    feedback: list[dict[str, Any]],
    frozen_facts: list[dict[str, Any]],
) -> ProposalResult:
    """Run exactly two proposer calls of four slots, falling back per invalid call."""
    if len(scaffolds) != 8:
        raise ValueError("Agent proposer requires exactly eight scaffold slots")
    calls = [
        _propose_four(
            mutator,
            generation=generation,
            scaffolds=scaffolds[index : index + 4],
            feedback=feedback,
            frozen_facts=frozen_facts,
        )
        for index in (0, 4)
    ]
    results = await asyncio.gather(*calls, return_exceptions=True)
    for result in results:
        if isinstance(result, (LoopControlError, asyncio.CancelledError)):
            raise result
    hypotheses: list[HypothesisSpec] = []
    cost = 0.0
    fallback_calls = 0
    diagnostics: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        fallback = scaffolds[index * 4 : index * 4 + 4]
        if isinstance(result, Exception):
            hypotheses.extend(fallback)
            fallback_calls += 1
            diagnostics.append({"batch": index, **_request_failure(result)})
        else:
            batch, batch_cost, reason = result
            hypotheses.extend(batch)
            cost += batch_cost
            fallback_calls += int(reason is not None)
            if reason is not None:
                diagnostics.append({"batch": index, **reason})
    return ProposalResult(tuple(hypotheses), cost, fallback_calls, tuple(diagnostics))


async def _propose_four(
    mutator: Mutator,
    *,
    generation: int,
    scaffolds: list[HypothesisSpec],
    feedback: list[dict[str, Any]],
    frozen_facts: list[dict[str, Any]],
) -> tuple[list[HypothesisSpec], float, dict[str, Any] | None]:
    evidence_ids = {
        evidence.split(":", 1)[0] for item in scaffolds for evidence in item.evidence_ids
    }
    projected_facts = [
        _project_fact(item)
        for item in frozen_facts
        if str(item.get("fact_id") or "") in evidence_ids
    ][:64]
    prompt = json.dumps(
        {
            "generation": generation,
            "slots": [item.model_dump(mode="json") for item in scaffolds],
            "aggregate_feedback": feedback,
            "output_contract": _output_contract(),
            "frozen_event_facts": projected_facts,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(prompt.encode()) + len(_SYSTEM_PROMPT.encode()) > mutator.max_input_utf8_bytes:
        raise _PromptBudgetExceeded("hypothesis proposer prompt exceeds frozen input budget")
    response = await mutator.llm_client.mutate(
        MutationRequest(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=mutator.max_output_tokens,
        )
    )
    cost = _cost(mutator, response.cache_metrics)
    try:
        payload = _parse_json_array(response.content)
        if len(payload) != 4:
            return scaffolds, cost, {"code": "wrong_batch_size"}
        proposals: list[HypothesisSpec] = []
        for slot, (scaffold, update) in enumerate(zip(scaffolds, payload, strict=True)):
            if not isinstance(update, dict):
                return scaffolds, cost, {"code": "entry_not_object", "slot": slot}
            safe_update = {key: value for key, value in update.items() if key in _ALLOWED_FIELDS}
            # Evidence, lineage, lane and compiler identity are platform-owned and cannot
            # be fabricated or changed by the model.
            safe_update.update(
                {
                    "hypothesis_id": str(uuid4()),
                    "evidence_ids": scaffold.evidence_ids,
                    "assets": scaffold.assets,
                    "asset_ids": scaffold.asset_ids,
                    "lane": scaffold.lane,
                    "lineage_kind": scaffold.lineage_kind,
                    "parent_ids": scaffold.parent_ids,
                    "dsl_version": scaffold.dsl_version,
                    "compiler_version": scaffold.compiler_version,
                }
            )
            base = scaffold.model_dump(mode="json")
            base.update(safe_update)
            try:
                proposals.append(HypothesisSpec.model_validate(base))
            except ValidationError as exc:
                return (
                    scaffolds,
                    cost,
                    {
                        "code": "dsl_validation_failed",
                        "slot": slot,
                        "fields": sorted(
                            {
                                str(error["loc"][0])
                                if error["loc"] and error["loc"][0] in _ALLOWED_FIELDS
                                else "contract"
                                for error in exc.errors(include_input=False, include_context=False)
                            }
                        ),
                        "violations": sorted(
                            {
                                error["type"]
                                if error["type"]
                                in {
                                    "missing",
                                    "extra_forbidden",
                                    "model_type",
                                    "float_parsing",
                                    "int_parsing",
                                    "literal_error",
                                    "greater_than",
                                    "greater_than_equal",
                                    "less_than",
                                    "less_than_equal",
                                    "string_too_short",
                                    "string_too_long",
                                    "list_type",
                                    "string_type",
                                    "float_type",
                                    "int_type",
                                    "bool_parsing",
                                    "too_short",
                                    "too_long",
                                    "value_error",
                                }
                                else "contract_error"
                                for error in exc.errors(include_input=False, include_context=False)
                            }
                        ),
                    },
                )
    except json.JSONDecodeError:
        return scaffolds, cost, {"code": "invalid_json_array", "kind": "syntax"}
    except (ValueError, TypeError):
        return (
            scaffolds,
            cost,
            {
                "code": "invalid_json_array",
                "kind": "empty" if not response.content.strip() else "missing_array",
            },
        )
    return proposals, cost, None


def _request_failure(error: Exception) -> dict[str, Any]:
    """Classify provider failures without retaining messages, URLs or response bodies."""
    if isinstance(error, _PromptBudgetExceeded):
        return {"code": "input_budget_exceeded"}
    if isinstance(error, (APITimeoutError, TimeoutError)):
        return {"code": "provider_timeout"}
    if isinstance(error, APIStatusError):
        return {"code": "provider_http_error", "status": int(error.status_code)}
    if isinstance(error, APIConnectionError):
        return {"code": "provider_connection_error"}
    return {"code": "request_failed"}


def _output_contract() -> dict[str, Any]:
    """Supply the exact mutable DSL schema; evidence and identity stay platform-owned."""
    schema = HypothesisSpec.model_json_schema()
    schema["properties"]["event_types"]["items"]["enum"] = sorted(_EVENT_TYPES)
    return {
        "type": "array",
        "minItems": 4,
        "maxItems": 4,
        "$defs": schema["$defs"],
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: value for key, value in schema["properties"].items() if key in _ALLOWED_FIELDS
            },
        },
        "instructions": (
            "Return compact valid JSON only, no commentary. Each item updates its corresponding slot. "
            "Omit unchanged fields. Nested objects must use exactly the schema's field names and types. "
            "Direct triggers are allowed only for listing/delisting/exploit/chain_halt. "
            "Do not output evidence, assets, lineage, identity or executable code."
        ),
    }


def _project_fact(fact: dict[str, Any]) -> dict[str, Any]:
    """Expose only typed fact fields; raw titles, actions, actors, and text never cross."""
    return {
        key: fact[key]
        for key in (
            "fact_id",
            "event_type",
            "assets",
            "asset_ids",
            "severity",
            "confidence",
            "effective_at",
            "available_at",
            "extractor_version",
            "policy_version",
            "retracted",
        )
        if key in fact
    }


def _parse_json_array(content: str) -> list[Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("hypothesis proposer returned no JSON array")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("hypothesis proposer payload must be an array")
    return parsed


def _cost(mutator: Mutator, metrics: Any) -> float:
    if mutator.input_usd_per_million is None or mutator.output_usd_per_million is None:
        return float(metrics.cost_usd)
    return float(
        (
            metrics.input_tokens * mutator.input_usd_per_million
            + metrics.output_tokens * mutator.output_usd_per_million
        )
        / 1_000_000
    )


__all__ = ["ProposalResult", "propose_generation"]

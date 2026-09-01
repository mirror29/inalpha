"""Owner-scoped Agent proposer for structured hypothesis DSL only."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from inalpha_shared_llm.types import MutationRequest  # type: ignore[import-untyped]

from ..mutator import Mutator
from .models import HypothesisSpec

_SYSTEM_PROMPT = """You are Inalpha's crypto strategy-hypothesis proposer.
Return only a JSON array of exactly four objects. Propose falsifiable event reaction mechanisms,
not prose strategies and never executable code. You may use only supplied frozen evidence IDs and
aggregate simulation feedback. Never infer publication time, unseen news, or future outcomes.
Each object may set: thesis,event_types,assets,applicable_regimes,direction,trigger_mode,
confirmation,invalidation,risk,counterfactual. Preserve diversity and avoid semantic duplicates."""

_ALLOWED_FIELDS = {
    "thesis",
    "event_types",
    "assets",
    "applicable_regimes",
    "direction",
    "trigger_mode",
    "confirmation",
    "invalidation",
    "risk",
    "counterfactual",
}


@dataclass(frozen=True, slots=True)
class ProposalResult:
    """Two-call proposal output plus measured provider cost."""

    hypotheses: tuple[HypothesisSpec, ...]
    cost_usd: float
    fallback_calls: int


async def propose_generation(
    mutator: Mutator,
    *,
    generation: int,
    scaffolds: list[HypothesisSpec],
    feedback: list[dict[str, Any]],
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
        )
        for index in (0, 4)
    ]
    results = await asyncio.gather(*calls, return_exceptions=True)
    hypotheses: list[HypothesisSpec] = []
    cost = 0.0
    fallback_calls = 0
    for index, result in enumerate(results):
        fallback = scaffolds[index * 4 : index * 4 + 4]
        if isinstance(result, Exception):
            hypotheses.extend(fallback)
            fallback_calls += 1
        else:
            batch, batch_cost, used_fallback = result
            hypotheses.extend(batch)
            cost += batch_cost
            fallback_calls += int(used_fallback)
    return ProposalResult(tuple(hypotheses), cost, fallback_calls)


async def _propose_four(
    mutator: Mutator,
    *,
    generation: int,
    scaffolds: list[HypothesisSpec],
    feedback: list[dict[str, Any]],
) -> tuple[list[HypothesisSpec], float, bool]:
    prompt = json.dumps(
        {
            "generation": generation,
            "slots": [item.model_dump(mode="json") for item in scaffolds],
            "aggregate_feedback": feedback,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(prompt.encode()) + len(_SYSTEM_PROMPT.encode()) > mutator.max_input_utf8_bytes:
        raise ValueError("hypothesis proposer prompt exceeds frozen input budget")
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
            raise ValueError("hypothesis proposer must return exactly four objects")
        proposals: list[HypothesisSpec] = []
        for scaffold, update in zip(scaffolds, payload, strict=True):
            if not isinstance(update, dict):
                raise ValueError("hypothesis proposal entries must be objects")
            safe_update = {key: value for key, value in update.items() if key in _ALLOWED_FIELDS}
            # Evidence, lineage, lane and compiler identity are platform-owned and cannot
            # be fabricated or changed by the model.
            safe_update.update(
                {
                    "hypothesis_id": str(uuid4()),
                    "evidence_ids": scaffold.evidence_ids,
                    "lane": scaffold.lane,
                    "lineage_kind": scaffold.lineage_kind,
                    "parent_ids": scaffold.parent_ids,
                    "dsl_version": scaffold.dsl_version,
                    "compiler_version": scaffold.compiler_version,
                }
            )
            base = scaffold.model_dump(mode="json")
            base.update(safe_update)
            proposals.append(HypothesisSpec.model_validate(base))
    except (ValueError, TypeError, json.JSONDecodeError):
        return scaffolds, cost, True
    return proposals, cost, False


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

"""Shared deterministic preparation for owner-approved and durable-loop campaigns."""

from datetime import UTC, datetime
from typing import Any

from inalpha_shared.errors import ValidationError

from .api.schemas import CreateCampaignRequest
from .hypothesis.compiler import compile_hypothesis, expand_implementations
from .hypothesis.models import HypothesisSpec
from .hypothesis.seeding import seed_generation_one
from .sandbox.ast_audit import assert_safe


def discovery_facts(snapshot: dict[str, Any], cutoff: datetime) -> list[dict[str, Any]]:
    """Keep facts first known by discovery close; malformed availability fails closed."""
    result = []
    for fact in snapshot.get("facts", []):
        available = datetime.fromisoformat(str(fact["available_at"]).replace("Z", "+00:00"))
        if available.tzinfo is None:
            raise ValueError("event availability must include a timezone")
        if available <= cutoff:
            result.append(fact)
    return result


def prepare_campaign(
    body: CreateCampaignRequest, snapshot: dict[str, Any],
    source_feedback: dict[str, Any] | None,
) -> tuple[list[HypothesisSpec], dict[str, Any]]:
    """Compile every initial arm and freeze policy; callers must separately verify authority."""
    hypotheses = body.hypotheses or seed_generation_one(
        snapshot, body.config.event_asset_code, body.config.asset_id,
    )
    if len(hypotheses) != 8:
        raise ValidationError(
            "event campaign requires exactly eight generation-one hypothesis slots",
            code="CAMPAIGN_DIRECTION_COVERAGE_REQUIRED",
        )
    for hypothesis in hypotheses:
        for implementation in expand_implementations(hypothesis):
            assert_safe(compile_hypothesis(implementation).source_code)
    frozen_config = {
        **body.config.model_dump(mode="json"),
        "event_snapshot": {
            key: snapshot[key] for key in (
                "snapshot_id", "events_sha256", "policy_version", "fact_count", "cutoff",
            )
        },
        "compiler_version": "event-strategy-compiler-v1",
        "selection_version": "pareto-novelty-v1",
        "hypotheses_source": "provided" if body.hypotheses else "discovery_scaffold",
        "evidence_threshold_version": "matched-events-v1",
        "minimum_matched_event_pairs": 8,
        "minimum_event_match_ratio": 0.70,
        "fdr_q": 0.10,
        "llm_call_topology": {"calls_per_generation": 2, "hypotheses_per_call": 4},
        "estimated_reserved_llm_cost_usd": 10 * body.llm.pricing.estimated_max_usd_per_candidate,
        "sealed_holdout_thresholds": {
            "sharpe_gt": 0, "net_return_pct_gt": 0,
            "max_drawdown_pct_lte": 25, "num_trades_gt": 0,
        },
        **({"source_simulation_feedback": source_feedback} if source_feedback is not None else {}),
        "created_at": datetime.now(UTC).isoformat(),
    }
    return hypotheses, frozen_config

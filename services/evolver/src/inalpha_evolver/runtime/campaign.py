"""Durable five-generation event campaign executor."""

from __future__ import annotations

import asyncio
import hashlib
import math
import statistics
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
from inalpha_paper.data_client import DataClient
from inalpha_paper.evaluation_executor import KillableEngineRunner
from inalpha_paper.event_conversion import market_event_from_fact
from inalpha_paper.evolution_execution_policy import frozen_protection_kwargs
from inalpha_paper.execution.exchange import EventExecutionPolicy
from inalpha_shared.db import get_conn

from ..api.schemas import CampaignConfig
from ..campaign_preparation import discovery_facts
from ..config import EvolverSettings
from ..data import FrozenBarsLoader, FrozenDataset
from ..data.persistent_snapshot import (
    decode_frozen_dataset,
    get_campaign_data_snapshot,
    persist_campaign_data_snapshot,
)
from ..evaluator.event_study import evaluate_event_reactions
from ..evaluator.frozen import FrozenDatasetEvaluator
from ..forward_client import create_forward_sandbox, get_forward_sandbox
from ..hypothesis.compiler import compile_hypothesis, expand_implementations
from ..hypothesis.models import HypothesisSpec
from ..hypothesis.proposer import propose_generation
from ..hypothesis.seeding import seed_generation_one
from ..hypothesis.selection import (
    HypothesisScore,
    ImplementationScore,
    benjamini_hochberg,
    block_bootstrap_p_value,
    credit_hypothesis,
    pareto_ranks,
    passes_generation_evidence_gate,
    plan_next_generation,
)
from ..loop_llm import campaign_model_scope
from ..mutator import Mutator
from ..owner_llm import build_owner_mutator
from ..storage import campaigns as store
from ..storage import proposal_checkpoints


async def execute_campaign(campaign: dict[str, Any], settings: EvolverSettings) -> None:
    """Run remaining generations, then lock one champion for isolated forward evidence."""
    if campaign.get("status") == "candidate_locked":
        await _ensure_forward_sandbox(campaign, settings)
        return
    if campaign.get("status") == "waiting_forward":
        await _reconcile_forward_sandbox(campaign, settings)
        return
    if campaign.get("status") == "holdout_ready":
        await _execute_sealed_holdout(campaign, settings)
        return
    config = _campaign_config(campaign)
    dataset, snapshot = await _load_frozen_inputs(campaign, config, settings)
    loop_scope = await campaign_model_scope(campaign)
    mutator = (
        await build_owner_mutator(campaign, settings, loop_scope=loop_scope)
        if loop_scope is not None else await build_owner_mutator(campaign, settings)
    )
    try:
        await _execute_campaign(
            campaign,
            settings,
            mutator,
            config=config,
            dataset=dataset,
            snapshot=snapshot,
        )
    finally:
        await mutator.close()


async def evaluate_sealed_holdout(
    campaign: dict[str, Any],
    *,
    source_code: str,
    hypothesis: HypothesisSpec,
    settings: EvolverSettings,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate the locked champion once on the campaign's untouched final 20%."""
    config = CampaignConfig.model_validate(
        {
            key: campaign["frozen_config"][key]
            for key in CampaignConfig.model_fields
            if key in campaign["frozen_config"]
        }
    )
    async with get_conn() as conn:
        frozen_row = await get_campaign_data_snapshot(conn, campaign["campaign_id"])
    if frozen_row is None:
        raise RuntimeError("sealed holdout requires the campaign's immutable data snapshot")
    dataset = decode_frozen_dataset(frozen_row)
    token = _service_token(campaign["owner_account_id"], settings)
    async with DataClient(
        settings.data_service_url,
        token,
        timeout=settings.evolver_data_timeout_s,
    ) as client:
        snapshot = await client.get_event_snapshot(str(campaign["event_snapshot_id"]))
    if len(dataset.bars) < 5:
        raise RuntimeError("sealed holdout requires at least five frozen bars")
    events = tuple(market_event_from_fact(item) for item in snapshot["facts"])
    evaluator = FrozenDatasetEvaluator(
        dataset=dataset,
        runner=KillableEngineRunner(
            timeout_s=settings.evolver_job_timeout_s,
            mem_gb=settings.evolver_job_mem_gb,
        ),
        initial_cash=config.initial_cash,
        fee_rate=config.fee_rate,
        validation_split=0.8,
        **frozen_protection_kwargs(campaign["frozen_config"]),
        funding_rate=config.funding_rate,
        trading_mode=config.trading_mode,
        leverage=config.leverage,
        events=events,
        event_execution_policy=EventExecutionPolicy(),
    )
    result = await evaluator.evaluate(source_code)
    validation = result.report.get("validation") or {}
    holdout = validation.get("holdout") or {}
    split_index = max(1, min(len(dataset.bars) - 1, int(len(dataset.bars) * 0.8)))
    holdout_bars = list(dataset.bars[split_index:])
    start_known_at = holdout_bars[0].bar_known_at
    holdout_events = [event for event in events if event.available_at >= start_known_at]
    event_study = evaluate_event_reactions(
        bars=holdout_bars,
        events=holdout_events,
        asset=config.event_asset_code,
        event_types=tuple(hypothesis.event_types),
        min_severity=hypothesis.risk.min_severity,
        min_confidence=hypothesis.risk.min_confidence,
        direction=hypothesis.direction,
        holding_bars=hypothesis.invalidation.holding_bars,
        exclusion_bars=hypothesis.counterfactual.exclusion_bars,
        volatility_tolerance=hypothesis.counterfactual.volatility_tolerance,
        volume_tolerance=hypothesis.counterfactual.volume_tolerance,
    )
    sharpe = holdout.get("sharpe")
    total_return = float(holdout.get("total_return_pct") or 0.0)
    max_drawdown = _metric_float(holdout.get("max_drawdown_pct"), default=100.0)
    passed = bool(
        isinstance(sharpe, (int, float))
        and sharpe > 0
        and total_return > 0
        and max_drawdown <= 25.0
        and int(holdout.get("num_trades") or 0) > 0
    )
    evidence = {
        "execution_model_version": config.execution_model_version,
        "snapshot_id": str(campaign["event_snapshot_id"]),
        "source_hash": hashlib.sha256(source_code.encode()).hexdigest(),
        "thresholds": {
            "sharpe_gt": 0,
            "net_return_pct_gt": 0,
            "max_drawdown_pct_lte": 25,
            "num_trades_gt": 0,
        },
        "metrics": {
            "sharpe": sharpe,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_drawdown,
            "num_trades": int(holdout.get("num_trades") or 0),
            "num_bars": int(holdout.get("num_bars") or len(holdout_bars)),
        },
        "event_study": event_study.as_dict(),
        "limited_evidence": event_study.event_count < 3,
    }
    return passed, evidence


async def _execute_campaign(
    campaign: dict[str, Any],
    settings: EvolverSettings,
    mutator: Mutator,
    *,
    config: CampaignConfig,
    dataset: FrozenDataset,
    snapshot: dict[str, Any],
) -> None:
    """Execute one credential-bound campaign while keeping the key process-local."""
    lease_token = UUID(str(campaign["lease_token"]))
    all_events = tuple(market_event_from_fact(item) for item in snapshot["facts"])
    search_dataset, validation_bars = _search_dataset(dataset)
    discovery_end = max(2, int(len(dataset.bars) * 0.60))
    discovery_cutoff = datetime.fromtimestamp(
        dataset.bars[discovery_end - 1].bar_known_at / 1e9, tz=UTC,
    )
    proposer_facts = discovery_facts(snapshot, discovery_cutoff)
    search_events = tuple(
        event
        for event in all_events
        if search_dataset.bars[0].bar_known_at
        <= event.available_at
        <= search_dataset.bars[-1].bar_known_at
    )
    evaluator = FrozenDatasetEvaluator(
        dataset=search_dataset,
        runner=KillableEngineRunner(
            timeout_s=settings.evolver_job_timeout_s,
            mem_gb=settings.evolver_job_mem_gb,
        ),
        initial_cash=config.initial_cash,
        fee_rate=config.fee_rate,
        validation_split=math.nextafter(discovery_end / len(search_dataset.bars), 1.0),
        **frozen_protection_kwargs(campaign["frozen_config"]),
        funding_rate=config.funding_rate,
        trading_mode=config.trading_mode,
        leverage=config.leverage,
        events=search_events,
        event_execution_policy=EventExecutionPolicy(),
    )
    generation = max(1, int(campaign["active_generation"]))
    while generation <= int(campaign["max_generations"]):
        async with get_conn() as conn:
            current = await store.get_campaign(
                conn, campaign["campaign_id"], campaign["owner_account_id"]
            )
        if current is None or current["status"] != "replaying":
            return
        if current.get("lease_token") != lease_token:
            raise RuntimeError("campaign lease fencing token was lost")
        hypotheses = [
            HypothesisSpec.model_validate(row["spec"])
            for row in current["hypotheses"]
            if int(row["generation"]) == generation
        ]
        if not hypotheses:
            raise RuntimeError(f"campaign generation {generation} has no hypotheses")
        has_started_generation = any(
            int(item["generation"]) == generation for item in current.get("implementations", [])
        )
        async with get_conn() as conn:
            committed_proposal = await proposal_checkpoints.get_proposal(
                conn, campaign["campaign_id"], generation,
            )
        if not has_started_generation and committed_proposal is None:
            if generation == 1 and current["frozen_config"].get("hypotheses_source") == "discovery_scaffold":
                hypotheses = seed_generation_one(
                    {**snapshot, "facts": proposer_facts}, config.event_asset_code, config.asset_id,
                )
            proposed = await propose_generation(
                mutator,
                generation=generation,
                scaffolds=hypotheses,
                feedback=_proposal_feedback(current, generation - 1),
                frozen_facts=proposer_facts,
            )
            hypotheses = list(proposed.hypotheses)
            async with get_conn() as conn:
                await proposal_checkpoints.commit_proposal(
                    conn, campaign_id=campaign["campaign_id"], generation=generation,
                    hypotheses=hypotheses, lease_token=lease_token,
                    cost_usd=proposed.cost_usd, fallback_calls=proposed.fallback_calls,
                )
        scores = await _evaluate_generation(
            campaign=current,
            generation=generation,
            hypotheses=hypotheses,
            evaluator=evaluator,
            validation_bars=list(validation_bars),
            validation_events=list(search_events),
            asset=config.event_asset_code,
            seed=config.random_seed + generation,
            max_concurrent=settings.candidate_evaluation_concurrency,
            lease_token=lease_token,
        )
        if generation == int(campaign["max_generations"]):
            async with get_conn() as conn:
                champion = await store.best_implementation(
                    conn, campaign["campaign_id"], generation
                )
                if champion is None:
                    await store.transition(
                        conn,
                        campaign["campaign_id"],
                        campaign["owner_account_id"],
                        from_statuses=("replaying",),
                        to_status="insufficient_evidence",
                        values={
                            "failure_code": "INSUFFICIENT_FDR_EVIDENCE",
                            "failure_message": "generation five produced no FDR-passing implementation",
                            "finished_at": datetime.now(UTC),
                        },
                        lease_token=lease_token,
                    )
                    return
                locked = await store.lock_champion(
                    conn,
                    campaign["campaign_id"],
                    campaign["owner_account_id"],
                    lease_token,
                )
            if locked is None:
                raise RuntimeError("campaign champion locking lost compare-and-swap")
            await _ensure_forward_sandbox(locked, settings)
            return
        next_generation = generation + 1
        next_hypotheses = _next_hypotheses(
            hypotheses,
            scores,
            seed=config.random_seed + next_generation,
        )
        async with get_conn() as conn:
            async with conn.transaction():
                await store.insert_hypotheses(
                    conn,
                    campaign["campaign_id"],
                    next_generation,
                    next_hypotheses,
                    lease_token=lease_token,
                )
                advanced = await store.advance_generation(
                    conn,
                    campaign["campaign_id"],
                    current_generation=generation,
                    next_generation=next_generation,
                    lease_token=lease_token,
                )
        if not advanced:
            raise RuntimeError("campaign generation advance lost compare-and-swap")
        generation = next_generation


def _proposal_feedback(campaign: dict[str, Any], generation: int) -> list[dict[str, Any]]:
    """Expose discovery metrics and discrete selection outcomes, never validation scores."""
    if generation < 1:
        frozen_config = campaign.get("frozen_config")
        source_feedback = (
            frozen_config.get("source_simulation_feedback")
            if isinstance(frozen_config, dict)
            else None
        )
        records = source_feedback.get("records") if isinstance(source_feedback, dict) else None
        return (
            [dict(item) for item in records if isinstance(item, dict)]
            if isinstance(records, list)
            else []
        )
    return [
        {
            "hypothesis_id": str(item["hypothesis_id"]),
            "lane": item["lane"],
            "selected": item.get("selected", False),
            "discovery_metrics": _discovery_metrics(campaign, item["hypothesis_id"], generation),
        }
        for item in campaign.get("hypotheses", [])
        if int(item["generation"]) == generation
    ]


def _discovery_metrics(campaign: dict[str, Any], hypothesis_id: Any, generation: int) -> dict[str, float]:
    """Summarize only each implementation's discovery segment, not its selection fitness."""
    trains = [
        (item.get("validation_metrics") or {}).get("train") or {}
        for item in campaign.get("implementations", [])
        if str(item["hypothesis_id"]) == str(hypothesis_id) and int(item["generation"]) == generation
    ]
    result = {}
    for key in ("sharpe", "total_return_pct", "max_drawdown_pct", "num_trades", "num_bars"):
        values = [
            float(train[key]) for train in trains
            if isinstance(train.get(key), (int, float)) and not isinstance(train[key], bool)
            and math.isfinite(float(train[key]))
        ]
        if values:
            result[key] = statistics.median(values)
    return result


async def _ensure_forward_sandbox(
    campaign: dict[str, Any],
    settings: EvolverSettings,
) -> None:
    """Retry the idempotent Paper handoff until the same sandbox is durably linked."""
    async with get_conn() as conn:
        current = await store.get_campaign(
            conn,
            campaign["campaign_id"],
            campaign["owner_account_id"],
        )
    if current is None or current["status"] != "candidate_locked":
        return
    lease_token = UUID(str(campaign["lease_token"]))
    if current.get("lease_token") != lease_token:
        raise RuntimeError("campaign lease fencing token was lost")
    sandbox = await create_forward_sandbox(current, settings)
    async with get_conn() as conn:
        linked = await store.start_forward_sandbox(
            conn,
            current["campaign_id"],
            current["owner_account_id"],
            lease_token=lease_token,
            sandbox_id=UUID(str(sandbox["sandbox_id"])),
        )
    if linked is None:
        raise RuntimeError("Paper Forward sandbox linking lost compare-and-swap")


async def _reconcile_forward_sandbox(
    campaign: dict[str, Any],
    settings: EvolverSettings,
) -> None:
    """Copy only signed Paper evidence into the campaign state machine."""
    evidence = await get_forward_sandbox(campaign, settings)
    async with get_conn() as conn:
        updated = await store.record_paper_forward(
            conn,
            campaign["campaign_id"],
            campaign["owner_account_id"],
            lease_token=UUID(str(campaign["lease_token"])),
            evidence=evidence,
        )
    if updated is None:
        raise RuntimeError("Paper Forward reconciliation lost compare-and-swap")


async def _execute_sealed_holdout(
    campaign: dict[str, Any],
    settings: EvolverSettings,
) -> None:
    """Automatically consume and evaluate the pre-committed champion exactly once."""
    owner = campaign["owner_account_id"]
    campaign_token = UUID(str(campaign["lease_token"]))
    async with get_conn() as conn:
        attempt = await store.reserve_holdout_attempt(
            conn,
            campaign["campaign_id"],
            owner,
            campaign_token,
        )
        if attempt is None or attempt["status"] in {"succeeded", "failed"}:
            return
        claimed = await store.claim_holdout_attempt(
            conn,
            attempt["attempt_id"],
            owner,
            ttl_s=max(60, settings.evolver_job_timeout_s + 30),
        )
        if claimed is None:
            return
        implementation = await store.holdout_attempt_input(
            conn,
            claimed["attempt_id"],
            owner,
            claimed["fencing_token"],
        )
    if implementation is None:
        raise RuntimeError("sealed holdout attempt lost its fencing token")
    try:
        passed, evidence = await evaluate_sealed_holdout(
            campaign,
            source_code=implementation["source_code"],
            hypothesis=HypothesisSpec.model_validate(implementation["spec"]),
            settings=settings,
        )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
        async with get_conn() as conn:
            await store.release_holdout_attempt(
                conn,
                claimed["attempt_id"],
                owner,
                claimed["fencing_token"],
            )
        raise
    except Exception as exc:
        passed = False
        evidence = {
            "error_code": str(getattr(exc, "code", "SEALED_HOLDOUT_FAILED")),
            "error_message": str(exc)[:1000],
        }
    async with get_conn() as conn:
        finalized = await store.finalize_holdout_attempt(
            conn,
            claimed["attempt_id"],
            owner,
            fencing_token=claimed["fencing_token"],
            passed=passed,
            evidence=evidence,
        )
    if finalized is None:
        raise RuntimeError("sealed holdout finalization lost compare-and-swap")


async def _evaluate_generation(
    *,
    campaign: dict[str, Any],
    generation: int,
    hypotheses: list[HypothesisSpec],
    evaluator: FrozenDatasetEvaluator,
    validation_bars: list[Any],
    validation_events: list[Any],
    asset: str,
    seed: int,
    max_concurrent: int,
    lease_token: UUID,
) -> list[HypothesisScore]:
    implementation_rows: list[tuple[dict[str, Any], HypothesisSpec, dict[str, Any]]] = []
    pending: list[tuple[dict[str, Any], HypothesisSpec, str]] = []
    for hypothesis in hypotheses:
        for implementation_index, implementation_spec in enumerate(
            expand_implementations(hypothesis)
        ):
            compiled = compile_hypothesis(implementation_spec)
            async with get_conn() as conn:
                row = await store.insert_implementation(
                    conn,
                    campaign_id=campaign["campaign_id"],
                    hypothesis_id=hypothesis.hypothesis_id,
                    generation=generation,
                    profile=_implementation_profile(
                        hypothesis, implementation_spec, implementation_index
                    ),
                    source_code=compiled.source_code,
                    source_hash=compiled.source_hash,
                    lease_token=lease_token,
                )
                cached = await store.find_cached_implementation(
                    conn, campaign["campaign_id"], compiled.source_hash
                )
            if row["outcome"] == "succeeded":
                implementation_rows.append((row, implementation_spec, row))
                continue
            if cached is not None and cached["implementation_id"] != row["implementation_id"]:
                values = {
                    key: cached[key]
                    for key in (
                        "fitness",
                        "validation_metrics",
                        "event_metrics",
                        "evidence_quality",
                        "novelty_score",
                        "fdr_pass",
                    )
                } | {"outcome": "succeeded"}
                async with get_conn() as conn:
                    updated = await store.update_implementation(
                        conn,
                        row["implementation_id"],
                        campaign_id=campaign["campaign_id"],
                        lease_token=lease_token,
                        values=values,
                    )
                assert updated is not None
                implementation_rows.append((updated, implementation_spec, updated))
                continue

            pending.append((row, implementation_spec, compiled.source_code))

    semaphore = asyncio.Semaphore(max_concurrent)

    async def evaluate_one(
        row: dict[str, Any],
        implementation_spec: HypothesisSpec,
        source_code: str,
    ) -> tuple[dict[str, Any], HypothesisSpec, dict[str, Any]]:
        async with semaphore:
            try:
                result = await evaluator.evaluate(
                    source_code,
                    event_scope={
                        "asset": asset,
                        "event_types": implementation_spec.event_types,
                        "min_severity": implementation_spec.risk.min_severity,
                        "min_confidence": implementation_spec.risk.min_confidence,
                        "holding_bars": implementation_spec.invalidation.holding_bars,
                    },
                )
                event_study = evaluate_event_reactions(
                    bars=validation_bars,
                    events=validation_events,
                    asset=asset,
                    event_types=tuple(implementation_spec.event_types),
                    min_severity=implementation_spec.risk.min_severity,
                    min_confidence=implementation_spec.risk.min_confidence,
                    direction=implementation_spec.direction,
                    holding_bars=implementation_spec.invalidation.holding_bars,
                    exclusion_bars=implementation_spec.counterfactual.exclusion_bars,
                    volatility_tolerance=implementation_spec.counterfactual.volatility_tolerance,
                    volume_tolerance=implementation_spec.counterfactual.volume_tolerance,
                )
                validation = result.report.get("validation") or {}
                holdout = validation.get("holdout") or {}
                holdout_sharpe = float(holdout.get("sharpe") or 0.0)
                holdout_return = float(holdout.get("total_return_pct") or 0.0)
                holdout_drawdown = _metric_float(
                    holdout.get("max_drawdown_pct"),
                    default=100.0,
                )
                selection_fitness = (
                    holdout_sharpe
                    + 0.02 * holdout_return
                    - max(0.0, holdout_drawdown - 20.0) / 20.0
                )
                execution_events = result.execution_event_metrics or {}
                event_metrics = {
                    **event_study.as_dict(),
                    **execution_events,
                    "scope_event_count": event_study.event_count,
                }
                if execution_events:
                    event_metrics["event_advantage_pct"] = float(
                        execution_events.get("mean_post_cost_return_pct") or 0.0
                    ) - float(event_study.mean_control_return_pct)
                match_ratio = event_study.matched_control_count / max(
                    1, event_study.event_count
                )
                match_ratio = min(1.0, match_ratio)
                evidence_quality = (
                    min(1.0, event_study.matched_control_count / 8.0) * match_ratio
                    if match_ratio >= 0.70
                    else 0.0
                )
                novelty = _spec_novelty(implementation_spec, hypotheses)
                values = {
                    "outcome": "succeeded",
                    "fitness": selection_fitness,
                    "validation_metrics": {
                        **validation,
                        "behavior_fingerprint": result.behavior,
                    },
                    "event_metrics": event_metrics,
                    "evidence_quality": evidence_quality,
                    "novelty_score": novelty,
                }
            except Exception as exc:
                values = {
                    "outcome": "failed",
                    "error_code": str(getattr(exc, "code", "CAMPAIGN_EVALUATION_FAILED")),
                    "error_message": str(exc)[:1000],
                }
            async with get_conn() as conn:
                updated = await store.update_implementation(
                    conn,
                    row["implementation_id"],
                    campaign_id=campaign["campaign_id"],
                    lease_token=lease_token,
                    values=values,
                )
            assert updated is not None
            return updated, implementation_spec, updated

    if pending:
        implementation_rows.extend(
            await asyncio.gather(
                *(evaluate_one(row, spec, source) for row, spec, source in pending)
            )
        )
    implementation_rows.sort(key=lambda item: str(item[0]["implementation_id"]))

    succeeded = [item for item in implementation_rows if item[0]["outcome"] == "succeeded"]
    _apply_behavior_novelty(succeeded, hypotheses)
    for row, _spec, _ in succeeded:
        async with get_conn() as conn:
            updated = await store.update_implementation(
                conn,
                row["implementation_id"],
                campaign_id=campaign["campaign_id"],
                lease_token=lease_token,
                values={"novelty_score": row["novelty_score"]},
            )
        assert updated is not None
    p_values = [
        block_bootstrap_p_value(
            (item[0].get("event_metrics") or {}).get("event_effects") or (),
            seed=seed + index,
        )
        for index, item in enumerate(succeeded)
    ]
    fdr_passes = benjamini_hochberg(p_values, q=0.10)
    for (row, _spec, _), fdr_pass in zip(succeeded, fdr_passes, strict=True):
        event_metrics = row.get("event_metrics") or {}
        evidence_pass = passes_generation_evidence_gate(
            event_count=int(event_metrics.get("scope_event_count") or 0),
            matched_control_count=int(event_metrics.get("matched_control_count") or 0),
        )
        async with get_conn() as conn:
            await store.update_implementation(
                conn,
                row["implementation_id"],
                campaign_id=campaign["campaign_id"],
                lease_token=lease_token,
                values={"fdr_pass": fdr_pass and evidence_pass},
            )

    by_hypothesis: dict[UUID, list[ImplementationScore]] = {}
    spec_by_id = {item.hypothesis_id: item for item in hypotheses}
    for row, implementation_spec, _ in succeeded:
        validation = row.get("validation_metrics") or {}
        event_metrics = row.get("event_metrics") or {}
        decay = validation.get("decay_ratio")
        by_hypothesis.setdefault(implementation_spec.hypothesis_id, []).append(
            ImplementationScore(
                fitness=float(row["fitness"]),
                max_drawdown_pct=_metric_float(
                    (validation.get("holdout") or {}).get("max_drawdown_pct"),
                    default=100.0,
                ),
                event_advantage=float(event_metrics.get("event_advantage_pct") or 0),
                stability=max(0.0, min(1.0, float(decay or 0))),
                evidence_quality=float(row.get("evidence_quality") or 0),
                novelty=float(row.get("novelty_score") or 0),
                complexity=min(1.0, len(row["source_code"]) / 20_000),
            )
        )
    hypothesis_scores: list[HypothesisScore] = []
    for hypothesis_id, implementations in by_hypothesis.items():
        spec = spec_by_id[hypothesis_id]
        hypothesis_scores.append(
            credit_hypothesis(
                hypothesis_id,
                lane=spec.lane,
                event_family="+".join(spec.event_types),
                trigger_mode=spec.trigger_mode,
                implementations=implementations,
            )
        )
    if not hypothesis_scores:
        raise RuntimeError("all campaign implementations failed")
    ranks = pareto_ranks(hypothesis_scores)
    selected_ids: set[UUID] = set()
    if len(hypothesis_scores) >= 2:
        plan = plan_next_generation(hypothesis_scores, seed=seed)
        selected_ids.update(plan.elites)
        selected_ids.update(plan.mutation_parents)
        selected_ids.update(plan.crossover_parents)
    else:
        selected_ids.add(hypothesis_scores[0].hypothesis_id)
    score_rows = [
        {
            "hypothesis_id": item.hypothesis_id,
            "upper_credit": item.credit,
            "novelty_score": item.objectives[-1],
            "pareto_rank": ranks[item.hypothesis_id],
            "selected": item.hypothesis_id in selected_ids,
        }
        for item in hypothesis_scores
    ]
    async with get_conn() as conn:
        await store.update_hypothesis_scores(
            conn,
            campaign["campaign_id"],
            generation,
            score_rows,
            lease_token,
        )
    return hypothesis_scores


def _next_hypotheses(
    current: list[HypothesisSpec],
    scores: list[HypothesisScore],
    *,
    seed: int,
) -> list[HypothesisSpec]:
    by_id = {item.hypothesis_id: item for item in current}
    if len(scores) == 1:
        parent = by_id[scores[0].hypothesis_id]
        return [
            _mutate(parent, index, lineage_kind="elite" if index < 2 else "mutation")
            if index < 7
            else _restart(seed, scope=parent)
            for index in range(8)
        ]
    plan = plan_next_generation(scores, seed=seed)
    out = [
        _clone(by_id[parent_id], lineage_kind="elite", parent_ids=[parent_id])
        for parent_id in plan.elites
    ]
    out.extend(
        _mutate(by_id[parent_id], index, lineage_kind="mutation")
        for index, parent_id in enumerate(plan.mutation_parents)
    )
    out.append(
        _crossover(
            by_id[plan.crossover_parents[0]],
            by_id[plan.crossover_parents[1]],
        )
    )
    out.append(_restart(seed, scope=current[0]))
    return out


def _clone(
    parent: HypothesisSpec,
    *,
    lineage_kind: str,
    parent_ids: list[UUID],
    updates: dict[str, Any] | None = None,
) -> HypothesisSpec:
    payload = parent.model_dump(mode="json")
    payload.update(updates or {})
    if payload["lane"] == "restart" and lineage_kind != "restart":
        payload["lane"] = "event_regime" if payload.get("applicable_regimes") else "event"
    payload.update(
        {
            "hypothesis_id": str(uuid4()),
            "lineage_kind": lineage_kind,
            "parent_ids": [str(item) for item in parent_ids],
        }
    )
    return HypothesisSpec.model_validate(payload)


def _mutate(
    parent: HypothesisSpec,
    index: int,
    *,
    lineage_kind: str,
) -> HypothesisSpec:
    confirmation = parent.confirmation.model_dump()
    invalidation = parent.invalidation.model_dump()
    risk = parent.risk.model_dump()
    variant = index % 4
    if variant == 0:
        confirmation["min_price_change_pct"] = max(0.0, confirmation["min_price_change_pct"] * 0.8)
        confirmation["min_volume_ratio"] = min(20.0, confirmation["min_volume_ratio"] * 1.1)
    elif variant == 1:
        confirmation["min_price_change_pct"] = min(30.0, confirmation["min_price_change_pct"] * 1.2)
        confirmation["min_volume_ratio"] = max(0.1, confirmation["min_volume_ratio"] * 0.9)
    elif variant == 2:
        invalidation["ttl_bars"] = min(100, invalidation["ttl_bars"] + 2)
        invalidation["holding_bars"] = max(1, int(invalidation["holding_bars"] * 0.75))
    else:
        risk["position_pct"] = max(0.01, risk["position_pct"] * 0.75)
        invalidation["max_adverse_pct"] = max(0.5, invalidation["max_adverse_pct"] * 0.8)
    return _clone(
        parent,
        lineage_kind=lineage_kind,
        parent_ids=[parent.hypothesis_id],
        updates={
            "thesis": f"{parent.thesis}；基于上一代验证反馈执行第 {variant + 1} 类定向变异。",
            "confirmation": confirmation,
            "invalidation": invalidation,
            "risk": risk,
        },
    )


def _crossover(left: HypothesisSpec, right: HypothesisSpec) -> HypothesisSpec:
    event_types = sorted(set(left.event_types) | set(right.event_types))
    direct_allowed = set(event_types) <= {"listing", "delisting", "exploit", "chain_halt"}
    payload = left.model_dump(mode="json")
    payload.update(
        {
            "hypothesis_id": str(uuid4()),
            "lineage_kind": "crossover",
            "parent_ids": [str(left.hypothesis_id), str(right.hypothesis_id)],
            "lane": "event_regime",
            "thesis": f"交叉验证两个不同机制：{left.thesis[:500]}；{right.thesis[:500]}",
            "event_types": event_types,
            "assets": sorted(set(left.assets) | set(right.assets)),
            "asset_ids": sorted(set(left.asset_ids) | set(right.asset_ids)),
            "evidence_ids": list(dict.fromkeys([*left.evidence_ids, *right.evidence_ids]))[:64],
            "trigger_mode": left.trigger_mode if direct_allowed else "confirmed",
            "risk": left.risk.model_copy(
                update={"position_pct": min(left.risk.position_pct, right.risk.position_pct)}
            ).model_dump(),
        }
    )
    return HypothesisSpec.model_validate(payload)


def _restart(seed: int, *, scope: HypothesisSpec) -> HypothesisSpec:
    templates = [
        ("listing", "long", "confirmed", "新上市事件在成交量确认后可能出现延迟价格发现。"),
        ("exploit", "short", "hybrid", "安全漏洞冲击可能先扩散后反转，分段确认能降低追空风险。"),
        ("chain_halt", "short", "confirmed", "链暂停会造成流动性折价，恢复前维持风险规避方向。"),
        ("upgrade", "long", "confirmed", "重大升级在价格与成交量共同确认后可能形成状态迁移。"),
    ]
    event_type, direction, mode, thesis = templates[seed % len(templates)]
    return HypothesisSpec(
        lane="restart",
        lineage_kind="restart",
        thesis=thesis,
        event_types=[event_type],
        assets=scope.assets,
        asset_ids=scope.asset_ids,
        direction=direction,  # type: ignore[arg-type]
        trigger_mode=mode,  # type: ignore[arg-type]
    )


def _spec_novelty(spec: HypothesisSpec, population: list[HypothesisSpec]) -> float:
    tokens = _spec_tokens(spec)
    distances = []
    for other in population:
        if other.hypothesis_id == spec.hypothesis_id:
            continue
        other_tokens = _spec_tokens(other)
        union = tokens | other_tokens
        similarity = len(tokens & other_tokens) / len(union) if union else 1.0
        distances.append(1.0 - similarity)
    return sum(distances) / len(distances) if distances else 1.0


def _apply_behavior_novelty(
    rows: list[tuple[dict[str, Any], HypothesisSpec, dict[str, Any]]],
    population: list[HypothesisSpec],
) -> None:
    """Blend semantic, signal, trade and holding distances before Pareto selection."""
    for row, spec, _ in rows:
        fingerprint = (row.get("validation_metrics") or {}).get("behavior_fingerprint") or {}
        distances: list[float] = []
        for other, _other_spec, _ in rows:
            if other["implementation_id"] == row["implementation_id"]:
                continue
            other_fingerprint = (
                (other.get("validation_metrics") or {}).get("behavior_fingerprint") or {}
            )
            signal = _vector_distance(fingerprint.get("signal"), other_fingerprint.get("signal"))
            trade = _vector_distance(fingerprint.get("trade"), other_fingerprint.get("trade"))
            holding = _vector_distance(
                fingerprint.get("holding"), other_fingerprint.get("holding")
            )
            distances.append(0.4 * signal + 0.3 * trade + 0.3 * holding)
        behavior = sum(distances) / len(distances) if distances else 1.0
        row["novelty_score"] = 0.4 * _spec_novelty(spec, population) + 0.6 * behavior


def _vector_distance(left: object, right: object) -> float:
    """Return normalized L1 distance for same-schema bounded behavior vectors."""
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return 1.0
    if not left:
        return 0.0
    return min(
        1.0,
        sum(abs(float(a) - float(b)) for a, b in zip(left, right, strict=True)) / len(left),
    )


def _spec_tokens(spec: HypothesisSpec) -> set[str]:
    words = set(spec.thesis.lower().replace("，", " ").replace("。", " ").split())
    return words | set(spec.event_types) | {spec.lane, spec.trigger_mode, spec.direction}


def _search_dataset(dataset: FrozenDataset) -> tuple[FrozenDataset, tuple[Any, ...]]:
    bars = dataset.bars
    discovery_end = max(2, int(len(bars) * 0.6))
    validation_end = max(discovery_end + 2, int(len(bars) * 0.8))
    validation_end = min(validation_end, len(bars) - 1)
    search_bars = bars[:validation_end]
    validation_bars = bars[discovery_end:validation_end]
    content_hash = hashlib.sha256(
        b"".join(
            f"{bar.bar_open_at}:{bar.bar_known_at}:{bar.open}:{bar.high}:{bar.low}:{bar.close}:{bar.volume}\n".encode()
            for bar in search_bars
        )
    ).hexdigest()
    manifest = dataset.manifest.model_copy(
        update={
            "effective_to": datetime.fromtimestamp(search_bars[-1].bar_open_at / 1e9, tz=UTC),
            "latest_bar_ts": datetime.fromtimestamp(search_bars[-1].bar_open_at / 1e9, tz=UTC),
            "bar_count": len(search_bars),
            "content_sha256": content_hash,
            "warnings": [*dataset.manifest.warnings, "sealed_holdout_excluded"],
        }
    )
    return FrozenDataset(tuple(search_bars), manifest), tuple(validation_bars)


def _campaign_config(campaign: dict[str, Any]) -> CampaignConfig:
    return CampaignConfig.model_validate(
        {
            key: campaign["frozen_config"][key]
            for key in CampaignConfig.model_fields
            if key in campaign["frozen_config"]
        }
    )


async def _load_frozen_inputs(
    campaign: dict[str, Any],
    config: CampaignConfig,
    settings: EvolverSettings,
) -> tuple[FrozenDataset, dict[str, Any]]:
    """Create bars once, then reuse their immutable payload on every restart."""
    async with get_conn() as conn:
        persisted = await get_campaign_data_snapshot(conn, campaign["campaign_id"])
    token = _service_token(campaign["owner_account_id"], settings)
    async with DataClient(
        settings.data_service_url,
        token,
        timeout=settings.evolver_data_timeout_s,
    ) as client:
        if persisted is None:
            loaded = await FrozenBarsLoader(client).load(
                venue=config.venue,
                symbol=config.symbol,
                timeframe=config.timeframe,
                from_ts=config.from_ts,
                as_of=config.as_of,
            )
            async with get_conn() as conn:
                async with conn.transaction():
                    persisted = await persist_campaign_data_snapshot(
                        conn,
                        campaign_id=campaign["campaign_id"],
                        owner_account_id=campaign["owner_account_id"],
                        lease_token=UUID(str(campaign["lease_token"])),
                        dataset=loaded,
                    )
        dataset = decode_frozen_dataset(persisted)
        snapshot = await client.get_event_snapshot(str(campaign["event_snapshot_id"]))
    return dataset, snapshot


def _implementation_profile(
    parent: HypothesisSpec,
    implementation: HypothesisSpec,
    index: int,
) -> str:
    """Keep three unique ablation identities even when direct mode is prohibited."""
    if parent.lane in {"event", "event_regime", "restart"} and set(
        parent.event_types
    ) <= {"listing", "delisting", "exploit", "chain_halt"}:
        return implementation.trigger_mode
    return ("canonical", "conservative", "aggressive")[index]


def _metric_float(value: object, *, default: float) -> float:
    """Preserve valid zero-valued metrics while defaulting only missing values."""
    return default if value is None else float(value)


def _service_token(account_id: UUID, settings: EvolverSettings) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(account_id),
            "token_use": "service",
            "service_audience": "data",
            "token_purpose": "event_snapshot_read",
            "owner_account_id": str(account_id),
            "iat": now,
            "exp": now + min(settings.service_token_ttl_s, 300),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


__all__ = ["evaluate_sealed_holdout", "execute_campaign"]

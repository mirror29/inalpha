"""Durable five-generation event campaign executor."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
from inalpha_paper.data_client import DataClient
from inalpha_paper.evaluation_executor import KillableEngineRunner
from inalpha_paper.event_conversion import market_event_from_fact
from inalpha_paper.execution.exchange import EventExecutionPolicy
from inalpha_shared.db import get_conn

from ..api.schemas import CampaignConfig
from ..config import EvolverSettings
from ..data import FrozenBarsLoader, FrozenDataset
from ..evaluator.event_study import evaluate_event_reactions
from ..evaluator.frozen import FrozenDatasetEvaluator
from ..hypothesis.compiler import compile_hypothesis, expand_implementations
from ..hypothesis.models import HypothesisSpec
from ..hypothesis.proposer import propose_generation
from ..hypothesis.selection import (
    HypothesisScore,
    ImplementationScore,
    benjamini_hochberg,
    block_bootstrap_p_value,
    credit_hypothesis,
    pareto_ranks,
    plan_next_generation,
)
from ..mutator import Mutator
from ..owner_llm import build_owner_mutator
from ..storage import campaigns as store


async def execute_campaign(campaign: dict[str, Any], settings: EvolverSettings) -> None:
    """Run remaining generations, then lock one champion for isolated forward evidence."""
    config = _campaign_config(campaign)
    dataset, snapshot = await _load_frozen_inputs(campaign, config, settings)
    mutator = await build_owner_mutator(campaign, settings)
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
    token = _service_token(campaign["owner_account_id"], settings)
    async with DataClient(
        settings.data_service_url,
        token,
        timeout=settings.evolver_data_timeout_s,
    ) as client:
        dataset = await FrozenBarsLoader(client).load(
            venue=config.venue,
            symbol=config.symbol,
            timeframe=config.timeframe,
            from_ts=config.from_ts,
            as_of=config.as_of,
        )
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
        asset=_base_asset(config.symbol),
        direction=hypothesis.direction,
        holding_bars=hypothesis.invalidation.holding_bars,
        exclusion_bars=hypothesis.counterfactual.exclusion_bars,
        volatility_tolerance=hypothesis.counterfactual.volatility_tolerance,
        volume_tolerance=hypothesis.counterfactual.volume_tolerance,
    )
    sharpe = holdout.get("sharpe")
    total_return = float(holdout.get("total_return_pct") or 0.0)
    max_drawdown = float(holdout.get("max_drawdown_pct") or 100.0)
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
    all_events = tuple(market_event_from_fact(item) for item in snapshot["facts"])
    search_dataset, validation_bars = _search_dataset(dataset)
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
        validation_split=0.75,
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
        if not has_started_generation:
            proposed = await propose_generation(
                mutator,
                generation=generation,
                scaffolds=hypotheses,
                feedback=_proposal_feedback(current, generation - 1),
            )
            hypotheses = list(proposed.hypotheses)
            async with get_conn() as conn:
                async with conn.transaction():
                    await store.replace_generation_hypotheses(
                        conn,
                        campaign["campaign_id"],
                        generation,
                        hypotheses,
                    )
                    await store.add_llm_cost(conn, campaign["campaign_id"], proposed.cost_usd)
        scores = await _evaluate_generation(
            campaign=current,
            generation=generation,
            hypotheses=hypotheses,
            evaluator=evaluator,
            validation_bars=list(validation_bars),
            validation_events=list(search_events),
            asset=config.symbol.split("/")[0],
            seed=config.random_seed + generation,
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
                        to_status="rejected",
                        values={
                            "failure_code": "NO_FDR_CHAMPION",
                            "failure_message": "generation five produced no FDR-passing implementation",
                            "finished_at": datetime.now(UTC),
                        },
                    )
                    return
                await store.lock_champion(
                    conn,
                    campaign["campaign_id"],
                    campaign["owner_account_id"],
                    champion["implementation_id"],
                )
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
                    conn, campaign["campaign_id"], next_generation, next_hypotheses
                )
                advanced = await store.advance_generation(
                    conn,
                    campaign["campaign_id"],
                    current_generation=generation,
                    next_generation=next_generation,
                )
        if not advanced:
            raise RuntimeError("campaign generation advance lost compare-and-swap")
        generation = next_generation


def _proposal_feedback(campaign: dict[str, Any], generation: int) -> list[dict[str, Any]]:
    """Expose only aggregate selection evidence, never bars, trades, or holdout rows."""
    if generation < 1:
        return []
    return [
        {
            "hypothesis_id": str(item["hypothesis_id"]),
            "lane": item["lane"],
            "upper_credit": item.get("upper_credit"),
            "novelty_score": item.get("novelty_score"),
            "pareto_rank": item.get("pareto_rank"),
            "selected": item.get("selected", False),
        }
        for item in campaign.get("hypotheses", [])
        if int(item["generation"]) == generation
    ]


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
) -> list[HypothesisScore]:
    implementation_rows: list[tuple[dict[str, Any], HypothesisSpec, dict[str, Any]]] = []
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
                        conn, row["implementation_id"], values=values
                    )
                assert updated is not None
                implementation_rows.append((updated, implementation_spec, updated))
                continue
            try:
                result = await evaluator.evaluate(compiled.source_code)
                event_study = evaluate_event_reactions(
                    bars=validation_bars,
                    events=validation_events,
                    asset=asset,
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
                holdout_drawdown = float(holdout.get("max_drawdown_pct") or 100.0)
                selection_fitness = (
                    holdout_sharpe
                    + 0.02 * holdout_return
                    - max(0.0, holdout_drawdown - 20.0) / 20.0
                )
                evidence_quality = min(1.0, event_study.event_count / 10.0) * (
                    sum(event.confidence for event in validation_events)
                    / max(1, len(validation_events))
                )
                novelty = _spec_novelty(implementation_spec, hypotheses)
                values = {
                    "outcome": "succeeded",
                    "fitness": selection_fitness,
                    "validation_metrics": validation,
                    "event_metrics": event_study.as_dict(),
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
                    conn, row["implementation_id"], values=values
                )
            assert updated is not None
            implementation_rows.append((updated, implementation_spec, updated))

    succeeded = [item for item in implementation_rows if item[0]["outcome"] == "succeeded"]
    p_values = [
        block_bootstrap_p_value(
            (item[0].get("event_metrics") or {}).get("event_effects") or (),
            seed=seed + index,
        )
        for index, item in enumerate(succeeded)
    ]
    fdr_passes = benjamini_hochberg(p_values, q=0.10)
    for (row, _spec, _), fdr_pass in zip(succeeded, fdr_passes, strict=True):
        async with get_conn() as conn:
            await store.update_implementation(
                conn, row["implementation_id"], values={"fdr_pass": fdr_pass}
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
                max_drawdown_pct=float(
                    (validation.get("holdout") or {}).get("max_drawdown_pct") or 100
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
        await store.update_hypothesis_scores(conn, campaign["campaign_id"], generation, score_rows)
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
            else _restart(seed)
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
    out.append(_restart(seed))
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
        payload["lane"] = (
            "event_regime" if payload.get("applicable_regimes") else "event"
        )
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
            "evidence_ids": list(dict.fromkeys([*left.evidence_ids, *right.evidence_ids]))[:64],
            "trigger_mode": left.trigger_mode if direct_allowed else "confirmed",
            "risk": left.risk.model_copy(
                update={"position_pct": min(left.risk.position_pct, right.risk.position_pct)}
            ).model_dump(),
        }
    )
    return HypothesisSpec.model_validate(payload)


def _restart(seed: int) -> HypothesisSpec:
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
    """Validate frozen market and event inputs before redeeming the owner LLM grant."""
    token = _service_token(campaign["owner_account_id"], settings)
    async with DataClient(
        settings.data_service_url,
        token,
        timeout=settings.evolver_data_timeout_s,
    ) as client:
        dataset = await FrozenBarsLoader(client).load(
            venue=config.venue,
            symbol=config.symbol,
            timeframe=config.timeframe,
            from_ts=config.from_ts,
            as_of=config.as_of,
        )
        snapshot = await client.get_event_snapshot(str(campaign["event_snapshot_id"]))
    return dataset, snapshot


def _implementation_profile(
    parent: HypothesisSpec,
    implementation: HypothesisSpec,
    index: int,
) -> str:
    """Keep three unique ablation identities even when direct mode is prohibited."""
    if set(parent.event_types) <= {"listing", "delisting", "exploit", "chain_halt"}:
        return implementation.trigger_mode
    return ("canonical", "conservative", "aggressive")[index]


def _service_token(account_id: UUID, settings: EvolverSettings) -> str:
    return jwt.encode(
        {
            "sub": str(account_id),
            "token_use": "service",
            "service_audience": "data",
            "token_purpose": "event_campaign_snapshot",
            "owner_account_id": str(account_id),
            "exp": int(time.time()) + settings.service_token_ttl_s,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _base_asset(symbol: str) -> str:
    """Normalize common slash and quote-suffixed crypto symbols for event matching."""
    normalized = symbol.upper().replace("-", "/")
    if "/" in normalized:
        return normalized.split("/", 1)[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)]
    return normalized


__all__ = ["evaluate_sealed_holdout", "execute_campaign"]

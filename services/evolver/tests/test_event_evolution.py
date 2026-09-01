"""Hypothesis DSL, statistical selection, and event-study unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.execution.exchange import EventExecutionPolicy
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.model.market_events import MarketEvent
from inalpha_paper.strategy_authoring import audit_strategy_code, load_strategy_class
from inalpha_shared_llm.types import CacheMetrics, MutationResponse

from inalpha_evolver.hypothesis.compiler import (
    canonical_spec_hash,
    compile_hypothesis,
    expand_implementations,
)
from inalpha_evolver.hypothesis.models import HypothesisSpec
from inalpha_evolver.hypothesis.proposer import propose_generation
from inalpha_evolver.hypothesis.seeding import seed_generation_one
from inalpha_evolver.hypothesis.selection import (
    HypothesisScore,
    ImplementationScore,
    _apply_niche_cap,
    benjamini_hochberg,
    block_bootstrap_p_value,
    credit_hypothesis,
    pareto_ranks,
    plan_next_generation,
)
from inalpha_evolver.mutator import Mutator
from inalpha_evolver.runtime.campaign import _clone, _mutate


def _spec() -> HypothesisSpec:
    return HypothesisSpec(
        lane="event",
        thesis="交易所上币后价格发现可能延迟，成交量确认能够过滤虚假反应。",
        evidence_ids=["fact-1:0"],
        event_types=["listing"],
        assets=["BTC"],
        direction="long",
        trigger_mode="confirmed",
    )


def _bars() -> list[Bar]:
    instrument = InstrumentId(symbol="BTC/USDT", venue="binance")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            instrument_id=instrument,
            timeframe="1h",
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            volume=1_000,
            ts_open=int((start + timedelta(hours=index)).timestamp() * 1e9),
            ts_event=int((start + timedelta(hours=index + 1)).timestamp() * 1e9),
            ts_init=int((start + timedelta(hours=index + 1)).timestamp() * 1e9),
        )
        for index in range(8)
    ]


def _event(bars: list[Bar]) -> MarketEvent:
    available_at = bars[1].bar_open_at + 30 * 60 * 1_000_000_000
    return MarketEvent(
        event_id="listing-1",
        event_type="listing",
        assets=("BTC",),
        action="exchange lists BTC",
        severity=1.0,
        confidence=1.0,
        effective_at=available_at,
        available_at=available_at,
    )


def _run_compiled(spec: HypothesisSpec) -> int:
    bars = _bars()
    compiled = compile_hypothesis(spec)
    strategy_class = load_strategy_class(compiled.source_code)
    engine = BacktestEngine(
        fee_rate=0,
        event_execution_policy=EventExecutionPolicy(),
    )
    strategy = strategy_class(
        f"compiled-{spec.trigger_mode}",
        engine.clock,
        engine.msgbus,
        instrument_id=bars[0].instrument_id,
    )
    engine.add_strategy(strategy)
    return len(engine.run(bars, events=[_event(bars)]).fills)


def _updated_spec(**updates: object) -> HypothesisSpec:
    payload = _spec().model_dump(mode="python")
    payload.update(updates)
    return HypothesisSpec.model_validate(payload)


def test_strong_event_expands_to_three_auditable_ablation_arms() -> None:
    spec = _spec()
    arms = expand_implementations(spec)
    assert [item.trigger_mode for item in arms] == ["direct", "confirmed", "hybrid"]
    for arm in arms:
        compiled = compile_hypothesis(arm)
        assert audit_strategy_code(compiled.source_code).ok
        assert load_strategy_class(compiled.source_code).__name__.startswith("EventHypothesis_")


def test_spec_hash_ignores_storage_identity_but_not_mechanism() -> None:
    spec = _spec()
    clone = spec.model_copy(update={"hypothesis_id": uuid4()})
    changed = clone.model_copy(update={"direction": "short"})
    assert canonical_spec_hash(spec) == canonical_spec_hash(clone)
    assert canonical_spec_hash(spec) != canonical_spec_hash(changed)


def test_generation_plan_has_fixed_2_4_1_1_topology() -> None:
    scores = [
        HypothesisScore(
            hypothesis_id=uuid4(),
            lane="event" if index < 4 else "regime",
            event_family=f"family-{index}",
            trigger_mode="confirmed",
            credit=float(index),
            objectives=(float(index), -index, 0.5, 0.2, 0.8, index / 10),
        )
        for index in range(8)
    ]
    plan = plan_next_generation(scores, seed=7)
    assert len(plan.elites) == 2
    assert len(plan.mutation_parents) == 4
    assert len(plan.crossover_parents) == 2
    assert plan.restart_slots == 1


def test_restart_parent_becomes_a_regular_lane_when_inherited() -> None:
    restart = HypothesisSpec(
        lane="restart",
        lineage_kind="restart",
        thesis="随机重启探索安全事件冲击后的延迟价格反应与成交量确认机制。",
        event_types=["exploit"],
        direction="short",
        trigger_mode="confirmed",
    )

    elite = _clone(
        restart,
        lineage_kind="elite",
        parent_ids=[restart.hypothesis_id],
    )
    mutation = _mutate(restart, 0, lineage_kind="mutation")

    assert elite.lane == "event"
    assert elite.lineage_kind == "elite"
    assert mutation.lane == "event"
    assert mutation.lineage_kind == "mutation"


def test_benjamini_hochberg_controls_the_whole_generation() -> None:
    assert benjamini_hochberg([0.001, 0.01, 0.04, 0.2], q=0.05) == [True, True, False, False]


@pytest.mark.parametrize(
    ("trigger_mode", "expected_trades"),
    [("direct", 1), ("confirmed", 1), ("hybrid", 2)],
)
def test_compiled_trigger_arms_execute_their_distinct_entry_paths(
    trigger_mode: str,
    expected_trades: int,
) -> None:
    spec = _updated_spec(
        trigger_mode=trigger_mode,
        confirmation={"min_price_change_pct": 0.1, "min_volume_ratio": 0.1},
    )

    assert _run_compiled(spec) == expected_trades


def test_compiled_confirmation_expires_at_ttl_without_a_trade() -> None:
    spec = _updated_spec(
        confirmation={"min_price_change_pct": 30.0, "min_volume_ratio": 20.0},
        invalidation={"ttl_bars": 1, "holding_bars": 12, "max_adverse_pct": 4.0},
    )

    assert _run_compiled(spec) == 0


def test_generation_one_seeds_all_eight_required_direction_lanes() -> None:
    snapshot = {
        "facts": [
            {"fact_id": "fact-exploit", "event_type": "exploit", "severity": 1.0},
            {"fact_id": "fact-listing", "event_type": "listing", "severity": 0.8},
            {"fact_id": "fact-halt", "event_type": "chain_halt", "severity": 0.9},
        ]
    }

    seeds = seed_generation_one(snapshot, "btc")

    assert len(seeds) == 8
    assert [seed.lane for seed in seeds] == [
        "event",
        "event",
        "event",
        "event_regime",
        "factor",
        "execution_risk",
        "regime",
        "restart",
    ]
    assert seeds[0].trigger_mode == "direct"
    assert seeds[-1].lineage_kind == "restart"
    assert all(seed.assets == ["BTC"] for seed in seeds)
    assert {item for seed in seeds[:3] for item in seed.evidence_ids} == {
        "fact-exploit:0",
        "fact-listing:0",
        "fact-halt:0",
    }


def test_credit_and_pareto_penalize_fragility_and_rank_dominance() -> None:
    hypothesis_id = uuid4()
    strong = ImplementationScore(2.0, 5.0, 1.0, 0.9, 0.9, 0.8, 0.1)
    weak = ImplementationScore(-2.0, 40.0, -1.0, 0.1, 0.1, 0.1, 0.9)
    stable = credit_hypothesis(
        hypothesis_id,
        lane="event",
        event_family="listing",
        trigger_mode="confirmed",
        implementations=[strong, strong],
    )
    fragile = credit_hypothesis(
        uuid4(),
        lane="event",
        event_family="listing",
        trigger_mode="confirmed",
        implementations=[strong, weak],
    )

    assert stable.credit > fragile.credit
    dominated = HypothesisScore(
        hypothesis_id=uuid4(),
        lane=fragile.lane,
        event_family=fragile.event_family,
        trigger_mode=fragile.trigger_mode,
        credit=fragile.credit,
        objectives=tuple(value - 1.0 for value in stable.objectives),
    )
    assert pareto_ranks([stable, dominated]) == {
        stable.hypothesis_id: 0,
        dominated.hypothesis_id: 1,
    }


def test_selection_caps_niches_and_rejects_invalid_statistics() -> None:
    same_niche = [
        HypothesisScore(
            hypothesis_id=uuid4(),
            lane="event",
            event_family="listing",
            trigger_mode="confirmed",
            credit=float(credit),
            objectives=(float(credit),) * 6,
        )
        for credit in (1, 3, 2)
    ]

    capped = _apply_niche_cap(same_niche, cap=2)

    assert [item.credit for item in capped] == [3.0, 2.0]
    assert block_bootstrap_p_value([], samples=100) == 1.0
    assert block_bootstrap_p_value([1.0, -0.5, 0.8], samples=100, seed=7) == (
        block_bootstrap_p_value([1.0, -0.5, 0.8], samples=100, seed=7)
    )
    with pytest.raises(ValueError, match="q must be within"):
        benjamini_hochberg([0.1], q=0)
    with pytest.raises(ValueError, match="p-values"):
        benjamini_hochberg([-0.1])
    with pytest.raises(ValueError, match="samples"):
        block_bootstrap_p_value([1.0], samples=99)


class _ProposalClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def mutate(self, _request: object) -> MutationResponse:
        self.calls += 1
        return MutationResponse(
            content=self.content,
            cache_metrics=CacheMetrics(input_tokens=100, output_tokens=40),
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_agent_proposer_uses_exactly_two_calls_and_preserves_platform_evidence() -> None:
    client = _ProposalClient(
        """[
        {"thesis":"事件发生后流动性重定价可能形成可证伪的延迟价格反应","trigger_mode":"confirmed"},
        {"thesis":"重大事件冲击可能存在需要成交量确认的延迟反应窗口","trigger_mode":"hybrid"},
        {"thesis":"高置信事件的价格反应持续时间可能显著长于低置信事件"},
        {"thesis":"使用波动状态约束事件触发条件可能减少无效交易和误报"}
        ]"""
    )
    scaffolds = [
        _spec().model_copy(
            update={
                "hypothesis_id": uuid4(),
                "evidence_ids": [f"fact-{index}:0"],
                "lane": "event" if index < 4 else "event_regime",
            }
        )
        for index in range(8)
    ]
    result = await propose_generation(
        Mutator(
            llm_client=client,  # type: ignore[arg-type]
            input_usd_per_million=1.0,
            output_usd_per_million=2.0,
        ),
        generation=1,
        scaffolds=scaffolds,
        feedback=[],
    )

    assert client.calls == 2
    assert len(result.hypotheses) == 8
    assert result.fallback_calls == 0
    assert result.cost_usd == pytest.approx(0.00036)
    assert [item.evidence_ids for item in result.hypotheses] == [
        item.evidence_ids for item in scaffolds
    ]
    assert [item.lane for item in result.hypotheses] == [item.lane for item in scaffolds]


@pytest.mark.asyncio
async def test_invalid_agent_batches_fall_back_without_losing_direction_coverage() -> None:
    client = _ProposalClient("not-json")
    scaffolds = [_spec().model_copy(update={"hypothesis_id": uuid4()}) for _ in range(8)]

    result = await propose_generation(
        Mutator(llm_client=client),  # type: ignore[arg-type]
        generation=2,
        scaffolds=scaffolds,
        feedback=[{"selected": True}],
    )

    assert client.calls == 2
    assert result.fallback_calls == 2
    assert result.hypotheses == tuple(scaffolds)

"""Deterministic upper-level credit, Pareto ranking, and next-generation plan."""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ImplementationScore:
    """Lower-level implementation evidence used to credit one hypothesis."""

    fitness: float
    max_drawdown_pct: float
    event_advantage: float
    stability: float
    evidence_quality: float
    novelty: float
    complexity: float


@dataclass(frozen=True, slots=True)
class HypothesisScore:
    """Upper-level multi-objective score and deterministic scalar credit."""

    hypothesis_id: UUID
    lane: str
    event_family: str
    trigger_mode: str
    credit: float
    objectives: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    """Exactly eight lineage operations for generations two through five."""

    elites: tuple[UUID, UUID]
    mutation_parents: tuple[UUID, UUID, UUID, UUID]
    crossover_parents: tuple[UUID, UUID]
    restart_slots: int = 1


def credit_hypothesis(
    hypothesis_id: UUID,
    *,
    lane: str,
    event_family: str,
    trigger_mode: str,
    implementations: Iterable[ImplementationScore],
) -> HypothesisScore:
    """Credit the genotype without hiding implementation fragility or drawdown."""
    scores = list(implementations)
    if not scores:
        raise ValueError("hypothesis credit requires at least one implementation")
    fitnesses = [item.fitness for item in scores]
    dispersion = statistics.pstdev(fitnesses) if len(fitnesses) > 1 else 0.0
    best = max(scores, key=lambda item: item.fitness)
    drawdown_penalty = max(0.0, best.max_drawdown_pct - 20.0) / 20.0
    evidence_penalty = max(0.0, 0.6 - best.evidence_quality)
    credit = (
        best.fitness
        + 0.35 * best.event_advantage
        + 0.20 * best.stability
        + 0.15 * best.novelty
        - 0.35 * dispersion
        - 0.10 * best.complexity
        - drawdown_penalty
        - evidence_penalty
    )
    objectives = (
        best.fitness,
        -best.max_drawdown_pct,
        best.stability,
        best.event_advantage,
        best.evidence_quality,
        best.novelty,
    )
    return HypothesisScore(
        hypothesis_id=hypothesis_id,
        lane=lane,
        event_family=event_family,
        trigger_mode=trigger_mode,
        credit=credit,
        objectives=objectives,
    )


def pareto_ranks(scores: Iterable[HypothesisScore]) -> dict[UUID, int]:
    """Assign non-dominated sorting ranks; all objectives are maximized."""
    remaining = list(scores)
    ranks: dict[UUID, int] = {}
    rank = 0
    while remaining:
        front = [
            candidate
            for candidate in remaining
            if not any(
                _dominates(other, candidate) for other in remaining if other is not candidate
            )
        ]
        if not front:
            raise RuntimeError("Pareto ranking failed to identify a front")
        for candidate in front:
            ranks[candidate.hypothesis_id] = rank
        front_ids = {candidate.hypothesis_id for candidate in front}
        remaining = [item for item in remaining if item.hypothesis_id not in front_ids]
        rank += 1
    return ranks


def plan_next_generation(
    scores: Iterable[HypothesisScore],
    *,
    seed: int,
) -> GenerationPlan:
    """Select 2 elites, 4 novelty-weighted mutations, 1 crossover, and 1 restart."""
    candidates = _apply_niche_cap(list(scores), cap=2)
    if len(candidates) < 2:
        raise ValueError("next generation requires at least two scored hypothesis families")
    ranks = pareto_ranks(candidates)
    ordered = sorted(
        candidates,
        key=lambda item: (ranks[item.hypothesis_id], -item.credit, str(item.hypothesis_id)),
    )
    elites = (ordered[0].hypothesis_id, ordered[1].hypothesis_id)
    rng = random.Random(seed)
    mutations = tuple(_novelty_tournament(candidates, ranks, rng).hypothesis_id for _ in range(4))
    left = _novelty_tournament(candidates, ranks, rng)
    distinct = [
        item
        for item in candidates
        if item.hypothesis_id != left.hypothesis_id
        and (item.lane != left.lane or item.event_family != left.event_family)
    ]
    right = _novelty_tournament(distinct or candidates, ranks, rng)
    return GenerationPlan(
        elites=elites,
        mutation_parents=mutations,  # type: ignore[arg-type]
        crossover_parents=(left.hypothesis_id, right.hypothesis_id),
    )


def benjamini_hochberg(p_values: Iterable[float], *, q: float = 0.10) -> list[bool]:
    """Return FDR rejections in original order using Benjamini-Hochberg."""
    values = list(p_values)
    if not 0 < q < 1:
        raise ValueError("q must be within (0,1)")
    if any(not 0 <= value <= 1 for value in values):
        raise ValueError("p-values must be within [0,1]")
    ordered = sorted(enumerate(values), key=lambda pair: (pair[1], pair[0]))
    cutoff_rank = 0
    for rank, (_, value) in enumerate(ordered, start=1):
        if value <= rank * q / max(1, len(values)):
            cutoff_rank = rank
    accepted = {index for index, _ in ordered[:cutoff_rank]}
    return [index in accepted for index in range(len(values))]


def block_bootstrap_p_value(
    effects: Iterable[float],
    *,
    block_size: int = 3,
    samples: int = 2_000,
    seed: int = 0,
) -> float:
    """Estimate one-sided P(mean<=0) while preserving short event clusters."""
    values = list(effects)
    if not values:
        return 1.0
    if block_size < 1 or samples < 100:
        raise ValueError("block_size must be positive and samples >= 100")
    blocks = [values[index : index + block_size] for index in range(0, len(values), block_size)]
    rng = random.Random(seed)
    non_positive = 0
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < len(values):
            sample.extend(rng.choice(blocks))
        if statistics.mean(sample[: len(values)]) <= 0:
            non_positive += 1
    return (non_positive + 1) / (samples + 1)


def _dominates(left: HypothesisScore, right: HypothesisScore) -> bool:
    return all(a >= b for a, b in zip(left.objectives, right.objectives, strict=True)) and any(
        a > b for a, b in zip(left.objectives, right.objectives, strict=True)
    )


def _apply_niche_cap(scores: list[HypothesisScore], *, cap: int) -> list[HypothesisScore]:
    buckets: dict[tuple[str, str, str], list[HypothesisScore]] = {}
    for item in scores:
        buckets.setdefault((item.lane, item.event_family, item.trigger_mode), []).append(item)
    return [
        item
        for bucket in buckets.values()
        for item in sorted(bucket, key=lambda value: (-value.credit, str(value.hypothesis_id)))[
            :cap
        ]
    ]


def _novelty_tournament(
    candidates: list[HypothesisScore],
    ranks: dict[UUID, int],
    rng: random.Random,
) -> HypothesisScore:
    if not candidates:
        raise ValueError("tournament candidate set is empty")
    contenders = rng.sample(candidates, k=min(3, len(candidates)))
    return max(
        contenders,
        key=lambda item: (
            -ranks[item.hypothesis_id],
            item.objectives[-1],
            item.credit,
        ),
    )


__all__ = [
    "GenerationPlan",
    "HypothesisScore",
    "ImplementationScore",
    "benjamini_hochberg",
    "block_bootstrap_p_value",
    "credit_hypothesis",
    "pareto_ranks",
    "plan_next_generation",
]

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class EvolutionRun:
    """一次演化会话的元数据。"""

    run_id: UUID = field(default_factory=uuid4)
    seed_strategy_id: str = ""
    budget: int = 4
    config: dict | None = None
    status: str = "running"
    llm_cost_usd: float = 0.0
    candidates_count: int = 0
    rejected_ast: int = 0
    rejected_contract: int = 0
    failed_eval: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class Candidate:
    """演化候选 —— 一次变异 + 评估的结果。"""

    candidate_id: UUID = field(default_factory=uuid4)
    run_id: UUID | None = None
    generation: int = 1
    parent_id: UUID | None = None
    source_code: str = ""
    source_hash: str = ""
    unified_diff: str | None = None
    mutation_hint: str | None = None
    llm_cost_usd: float | None = None
    cache_hit_tokens: int | None = None
    fitness: float | None = None
    report: dict | None = None
    overfitting_risk: str = "high"
    data_epoch: int = 0
    status: str = "evaluated"
    created_at: datetime | None = None


@dataclass(slots=True)
class EvaluationResult:
    """单次回测评估的结果。"""

    report: dict
    """序列化的 BacktestReport（含 equity_curve / sharpe / calmar / ...）。"""
    fitness: float
    """合成的多目标适应度分数。"""
    data_epoch: int
    """回测数据的时间戳（UNIX ms，用于过拟合检测）。"""
    overfitting_risk: str = "high"
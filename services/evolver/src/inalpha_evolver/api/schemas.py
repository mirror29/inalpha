"""Evolver API schemas（Pydantic 模型）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EvolutionConfig(BaseModel):
    """演化运行配置。"""

    universe: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    period_from: str = "2025-01-01"
    period_to: str = "2025-12-31"
    timeframe: str = "1h"
    initial_cash: float = 10000.0


class StartRunRequest(BaseModel):
    """启动演化运行的请求体。"""

    seed_strategy_id: str = "sma_cross_v1"
    budget: int = Field(default=4, ge=1, le=100)
    config: EvolutionConfig = Field(default_factory=EvolutionConfig)


class CandidateResponse(BaseModel):
    """候选策略的响应模型。"""

    candidate_id: UUID
    run_id: UUID
    generation: int
    parent_id: UUID | None = None
    source_code: str
    source_hash: str
    mutation_hint: str | None = None
    fitness: float | None = None
    report: dict[str, Any] | None = None
    overfitting_risk: str = "high"
    status: str = "evaluated"
    created_at: datetime | None = None


class RunStatusResponse(BaseModel):
    """演化运行状态响应。"""

    run_id: UUID
    seed_strategy_id: str
    budget: int
    config: dict[str, Any] | None = None
    status: str
    llm_cost_usd: float = 0.0
    candidates_count: int = 0
    rejected_ast: int = 0
    rejected_contract: int = 0
    failed_eval: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    candidates: list[CandidateResponse] = Field(default_factory=list)
"""Evolver API 请求与响应模型。"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ..data.datetime_policy import MAX_AS_OF_CLOCK_SKEW
from ..data.manifest import DatasetManifest
from ..hypothesis.models import HypothesisSpec


class EvolutionConfig(BaseModel):
    venue: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(pattern=r"^\d+(m|h|d|wk|mo)$")
    from_ts: datetime
    as_of: datetime
    initial_cash: float = Field(default=10_000.0, ge=100)
    fee_rate: float = Field(default=0.001, ge=0, le=0.1)
    validation_split: float = Field(default=0.3, ge=0, le=0.5)
    trading_mode: Literal["spot", "perp"] = "spot"
    leverage: int = Field(default=1, ge=1, le=20)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    funding_rate: float = Field(default=0, ge=-0.1, le=0.1)

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Bound strategy constructor input and reject non-JSON numeric values."""
        if len(json.dumps(value, allow_nan=False).encode()) > 16_384:
            raise ValueError("strategy params exceed 16 KiB")
        return value

    @field_validator("from_ts", "as_of")
    @classmethod
    def normalize_datetimes(cls, value: datetime, info: ValidationInfo) -> datetime:
        """拒绝无时区 cutoff，其余时间统一为 UTC。"""
        if value.tzinfo is None:
            if info.field_name == "as_of":
                raise ValueError("as_of must be timezone-aware")
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> EvolutionConfig:
        if self.trading_mode == "spot" and self.leverage != 1:
            raise ValueError("spot evolution requires leverage=1")
        if self.as_of > datetime.now(UTC) + MAX_AS_OF_CLOCK_SKEW:
            raise ValueError("as_of exceeds trusted current time")
        if self.from_ts >= self.as_of:
            raise ValueError("from_ts must be earlier than as_of")
        if self.as_of - self.from_ts > timedelta(days=3650):
            raise ValueError("evolution window cannot exceed 10 years")
        return self


class EvolutionPricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=80)
    currency: Literal["USD"]
    input_usd_per_million: float = Field(gt=0)
    output_usd_per_million: float = Field(gt=0)
    assumed_input_tokens: int = Field(gt=0, le=1_000_000)
    max_output_tokens: int = Field(gt=0, le=100_000)
    estimated_max_usd_per_candidate: float = Field(gt=0)


class EvolutionLLMSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1, max_length=128)
    provider: Literal["deepseek", "openai", "kimi", "zhipu"]
    model: str = Field(min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=500)
    pricing: EvolutionPricingSnapshot
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("LLM base_url must be an absolute HTTP URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("LLM base_url cannot contain credentials, query, or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def verify_config_digest(self) -> EvolutionLLMSnapshot:
        """拒绝任何未被 Mastra 审批摘要覆盖的快照字段变更。"""
        official_base_urls = {
            "deepseek": "https://api.deepseek.com",
            "openai": "https://api.openai.com/v1",
            "kimi": "https://api.moonshot.cn/v1",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        }
        if self.provider == "deepseek" and self.base_url == "https://api.deepseek.com/v1":
            self.base_url = "https://api.deepseek.com"
        if self.base_url != official_base_urls[self.provider]:
            raise PydanticCustomError(
                "llm_endpoint_unavailable",
                "evolution requires the official {provider} API endpoint",
                {"provider": self.provider},
            )
        priced_models = {
            "deepseek": ("deepseek-flash", 0.3, 1.2),
            "openai": ("gpt-5.5", 5.0, 15.0),
            "kimi": ("kimi-k2.6", 0.6, 2.5),
            "zhipu": ("glm-5.2", 0.7, 2.8),
        }
        expected_model, expected_input_rate, expected_output_rate = priced_models[self.provider]
        pricing = self.pricing
        expected_max = (
            pricing.assumed_input_tokens * expected_input_rate
            + pricing.max_output_tokens * expected_output_rate
        ) / 1_000_000
        if (
            self.model != expected_model
            or pricing.version != "provider-estimate-2026-09"
            or pricing.assumed_input_tokens != 24_000
            or pricing.max_output_tokens != 8_192
            or pricing.input_usd_per_million != expected_input_rate
            or pricing.output_usd_per_million != expected_output_rate
            or abs(pricing.estimated_max_usd_per_candidate - expected_max) > 1e-12
        ):
            raise PydanticCustomError(
                "llm_pricing_unavailable",
                "evolution pricing is unavailable for model {provider}/{model}",
                {"provider": self.provider, "model": self.model},
            )
        expected = compute_llm_config_digest(self)
        if not hmac.compare_digest(self.config_digest, expected):
            raise PydanticCustomError(
                "llm_snapshot_digest",
                "LLM config_digest does not match the frozen snapshot",
            )
        return self


class StoredEvolutionLLMSnapshot(BaseModel):
    """Read model for an immutable snapshot accepted by an earlier pricing catalog."""

    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1, max_length=128)
    provider: Literal["deepseek", "openai", "kimi", "zhipu"]
    model: str = Field(min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=500)
    pricing: EvolutionPricingSnapshot
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        """Keep response data structurally safe without reapplying today's catalog."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("LLM base_url must be an absolute HTTP URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("LLM base_url cannot contain credentials, query, or fragment")
        return value.rstrip("/")


def compute_llm_config_digest(snapshot: EvolutionLLMSnapshot) -> str:
    """按与 TypeScript 相同的字段顺序和数字文本计算跨语言摘要。"""
    pricing = snapshot.pricing
    canonical = [
        snapshot.config_id,
        snapshot.provider,
        snapshot.model,
        snapshot.base_url,
        pricing.version,
        pricing.currency,
        _number_text(pricing.input_usd_per_million),
        _number_text(pricing.output_usd_per_million),
        _number_text(pricing.assumed_input_tokens),
        _number_text(pricing.max_output_tokens),
        _number_text(pricing.estimated_max_usd_per_candidate),
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def campaign_request_digest(request: CreateCampaignRequest) -> str:
    """Bind the credential grant to every campaign input affecting cost or results."""
    config = request.config
    hypotheses_hash = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in request.hypotheses],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    canonical = [
        request.target_kind or "",
        request.target_id or "",
        str(request.event_snapshot_id),
        str(request.source_run_id or ""),
        config.venue,
        config.symbol,
        config.asset_id,
        config.event_asset_code,
        config.timeframe,
        str(int(config.from_ts.timestamp() * 1_000)),
        str(int(config.as_of.timestamp() * 1_000)),
        struct.pack(">d", config.initial_cash).hex(),
        struct.pack(">d", config.fee_rate).hex(),
        config.trading_mode,
        str(config.leverage),
        struct.pack(">d", config.discovery_ratio).hex(),
        struct.pack(">d", config.generation_validation_ratio).hex(),
        struct.pack(">d", config.sealed_holdout_ratio).hex(),
        config.execution_model_version,
        config.control_matcher_version,
        str(config.random_seed),
        request.llm.config_digest,
        hypotheses_hash,
    ]
    if config.funding_rate:
        canonical.append(["funding_rate", struct.pack(">d", config.funding_rate).hex()])
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _number_text(value: int | float) -> str:
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class StartRunRequest(BaseModel):
    seed_strategy_id: str = Field(default="sma_cross_v1", max_length=128)
    budget: int = Field(default=4, ge=1, le=24)
    config: EvolutionConfig
    llm: EvolutionLLMSnapshot


class CandidateResponse(BaseModel):
    candidate_id: UUID
    run_id: UUID
    slot: int
    generation: int
    stage: str
    outcome: str
    source_code: str | None = None
    source_hash: str | None = None
    unified_diff: str | None = None
    mutation_hint: str | None = None
    llm_cost_usd: float | None = None
    fitness: float | None = None
    evaluation_snapshot: dict[str, Any] | None = None
    audit_snapshot: dict[str, Any] | None = None
    contract_snapshot: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    overfitting_risk: str | None = None
    data_epoch: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunStatusResponse(BaseModel):
    run_id: UUID
    seed_strategy_id: str
    budget: int
    config: dict[str, Any]
    llm_snapshot: StoredEvolutionLLMSnapshot | None = None
    llm_config_digest: str | None = None
    status: str
    active_stage: str | None = None
    llm_cost_usd: float = 0.0
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    dataset_manifest: DatasetManifest | None = None
    seed_report_snapshot: dict[str, Any] | None = None
    baseline_snapshot: dict[str, Any] | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    attempted: int = 0
    succeeded: int = 0
    rejected: int = 0
    candidates: list[CandidateResponse] = Field(default_factory=list)


class RunListResponse(BaseModel):
    items: list[RunStatusResponse]
    next_cursor: str | None = None


class CampaignConfig(BaseModel):
    """Frozen statistical and execution contract for one E2 campaign."""

    model_config = ConfigDict(extra="forbid")

    venue: str = Field(default="binance", min_length=1, max_length=40)
    symbol: str = Field(default="BTC/USDT", min_length=1, max_length=80)
    asset_id: str = Field(min_length=7, max_length=160, pattern=r"^asset:[A-Z0-9._:-]+$")
    event_asset_code: str = Field(min_length=1, max_length=120)
    timeframe: Literal["15m", "1h", "4h"] = "1h"
    from_ts: datetime
    as_of: datetime
    initial_cash: float = Field(default=10_000.0, ge=100)
    fee_rate: float = Field(default=0.001, ge=0, le=0.1)
    trading_mode: Literal["spot", "perp"] = "perp"
    leverage: int = Field(default=1, ge=1, le=20)
    funding_rate: float = Field(default=0, ge=-0.1, le=0.1)
    discovery_ratio: float = 0.6
    generation_validation_ratio: float = 0.2
    sealed_holdout_ratio: float = 0.2
    execution_model_version: Literal["event-fill-v1"] = "event-fill-v1"
    control_matcher_version: Literal["event-control-v1"] = "event-control-v1"
    random_seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @field_validator("from_ts", "as_of", mode="after")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> CampaignConfig:
        if self.from_ts >= self.as_of:
            raise ValueError("from_ts must be earlier than as_of")
        if (
            self.discovery_ratio,
            self.generation_validation_ratio,
            self.sealed_holdout_ratio,
        ) != (0.6, 0.2, 0.2):
            raise ValueError("campaign split is frozen at 60/20/20")
        if self.trading_mode == "spot" and self.leverage != 1:
            raise ValueError("spot campaigns must use leverage=1")
        return self


class CreateCampaignRequest(BaseModel):
    """Create a manual-hypothesis vertical or generation-one campaign."""

    model_config = ConfigDict(extra="forbid")

    event_snapshot_id: UUID
    source_run_id: UUID | None = None
    target_kind: Literal[
        "strategy_candidate",
        "paper_runner",
        "backtest_run",
        "e1_run",
        "e1_candidate",
        "e2_campaign",
    ] | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    config: CampaignConfig
    llm: EvolutionLLMSnapshot
    hypotheses: list[HypothesisSpec] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unique_hypotheses(self) -> CreateCampaignRequest:
        ids = [item.hypothesis_id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis_id values must be unique")
        if (self.target_kind is None) != (self.target_id is None):
            raise ValueError("target_kind and target_id must be provided together")
        return self


class GenerationProjection(BaseModel):
    generation: int
    hypothesis_count: int
    selected_count: int
    best_credit: float | None = None
    best_novelty: float | None = None


class HypothesisResponse(BaseModel):
    hypothesis_id: UUID
    campaign_id: UUID
    generation: int
    slot: int
    lineage_kind: str
    lane: str
    parent_ids: list[UUID]
    spec: dict[str, Any]
    spec_hash: str
    upper_credit: float | None = None
    novelty_score: float | None = None
    pareto_rank: int | None = None
    selected: bool = False
    created_at: datetime


class ImplementationResponse(BaseModel):
    implementation_id: UUID
    campaign_id: UUID
    hypothesis_id: UUID
    generation: int
    profile: str
    source_hash: str
    outcome: str
    fitness: float | None = None
    validation_metrics: dict[str, Any] | None = None
    event_metrics: dict[str, Any] | None = None
    evidence_quality: float | None = None
    novelty_score: float | None = None
    fdr_pass: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class CampaignResponse(BaseModel):
    campaign_id: UUID
    owner_account_id: UUID
    source_run_id: UUID | None = None
    status: str
    active_generation: int
    hypothesis_budget: int
    implementations_per_hypothesis: int
    max_generations: int
    event_snapshot_id: UUID
    data_snapshot_id: UUID | None = None
    frozen_config: dict[str, Any]
    llm_snapshot: StoredEvolutionLLMSnapshot
    llm_config_digest: str
    llm_cost_usd: float
    locked_candidate_id: UUID | None = None
    holdout_consumed_at: datetime | None = None
    forward_sandbox_id: UUID | None = None
    forward_started_at: datetime | None = None
    forward_deadline_at: datetime | None = None
    forward_event_count: int = 0
    forward_metrics: dict[str, Any] | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    state_version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    generations: list[GenerationProjection] = Field(default_factory=list)
    hypotheses: list[HypothesisResponse] = Field(default_factory=list)


class ImplementationPageResponse(BaseModel):
    """Bounded source-free candidate evidence for lazy dashboard reads."""

    items: list[ImplementationResponse]
    limit: int
    offset: int
    has_more: bool
    implementations: list[ImplementationResponse] = Field(default_factory=list)


class CampaignListResponse(BaseModel):
    items: list[CampaignResponse]


class EvolutionCapabilitiesResponse(BaseModel):
    """Authoritative feature gate and frozen E2 operating limits."""

    event_evolution_enabled: bool
    durable_loop_enabled: bool = False
    durable_loop_reason: str | None = "durable loop dispatcher unavailable"
    reason: str | None = None
    max_generations: Literal[5] = 5
    hypotheses_per_generation: Literal[8] = 8
    implementations_per_hypothesis: Literal[3] = 3
    candidate_concurrency: int = Field(ge=1, le=4)
    supported_timeframes: list[Literal["15m", "1h", "4h"]]
    automatic_stage_approval: bool = True
    automatic_promotion: bool = False
    runner_eligible: bool = False


class AdoptionResponse(BaseModel):
    adoption_id: UUID
    artifact_id: UUID
    owner_account_id: UUID
    campaign_id: UUID | None
    evidence_grade: Literal["standard", "limited"]
    status: Literal["experimental", "accepted", "rejected"]
    runner_eligible: Literal[False]
    evidence: dict[str, Any]
    adopted_at: datetime


class AdoptionSummaryResponse(AdoptionResponse):
    source_hash: str
    compiler_version: str | None = None
    campaign_status: str | None = None


class AdoptionListResponse(BaseModel):
    items: list[AdoptionSummaryResponse]


class EvolutionLoopBudgetUsage(BaseModel):
    """Ledger totals in USD, excluding credentials and authorization internals."""

    max_cost_usd: float
    spent_usd: float
    reserved_usd: float
    available_usd: float


class EvolutionLoopResponse(BaseModel):
    """Compact durable workflow projection; heavy campaign details remain lazy."""

    loop_id: UUID
    owner_account_id: UUID
    requested_by_sub: str
    operation_id: str
    target_kind: str
    target_id: str
    target_snapshot: dict[str, Any]
    status: str
    e1_run_id: UUID | None = None
    campaign_id: UUID | None = None
    forward_sandbox_id: UUID | None = None
    holdout_attempt_id: UUID | None = None
    frozen_config: dict[str, Any]
    budget: dict[str, Any]
    budget_usage: EvolutionLoopBudgetUsage | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    state_version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class EvolutionLoopListResponse(BaseModel):
    items: list[EvolutionLoopResponse]


class EvolutionLoopEventResponse(BaseModel):
    loop_id: UUID
    version: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class EvolutionLoopEventListResponse(BaseModel):
    items: list[EvolutionLoopEventResponse]

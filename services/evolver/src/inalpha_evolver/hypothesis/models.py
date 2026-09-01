"""Typed, falsifiable upper-level strategy hypothesis contract."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HypothesisLane = Literal["event", "event_regime", "factor", "execution_risk", "regime", "restart"]
TriggerMode = Literal["direct", "confirmed", "hybrid"]
LineageKind = Literal["seed", "elite", "mutation", "crossover", "restart"]
Direction = Literal["long", "short"]

_EVENT_TYPES = {
    "listing",
    "delisting",
    "exploit",
    "chain_halt",
    "regulatory",
    "upgrade",
    "unlock",
    "burn",
    "partnership",
    "macro",
    "other",
}
_DIRECT_ALLOWED = {"listing", "delisting", "exploit", "chain_halt"}


class ConfirmationSpec(BaseModel):
    """Price/volume confirmation evaluated only on closed bars."""

    model_config = ConfigDict(extra="forbid")

    lookback_bars: int = Field(default=12, ge=2, le=100)
    min_price_change_pct: float = Field(default=0.5, ge=0, le=30)
    min_volume_ratio: float = Field(default=1.2, ge=0.1, le=20)


class InvalidationSpec(BaseModel):
    """Pre-registered exit and expiry conditions."""

    model_config = ConfigDict(extra="forbid")

    ttl_bars: int = Field(default=6, ge=1, le=100)
    holding_bars: int = Field(default=12, ge=1, le=500)
    max_adverse_pct: float = Field(default=4.0, gt=0, le=50)


class RiskSpec(BaseModel):
    """Bounded strategy-level exposure; framework risk remains authoritative."""

    model_config = ConfigDict(extra="forbid")

    position_pct: float = Field(default=0.10, gt=0, le=0.25)
    hybrid_initial_fraction: float = Field(default=0.25, gt=0, lt=1)
    min_severity: float = Field(default=0.5, ge=0, le=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)


class CounterfactualSpec(BaseModel):
    """Matching rules for event-free comparison windows."""

    model_config = ConfigDict(extra="forbid")

    match_regime: bool = True
    volatility_tolerance: float = Field(default=0.20, gt=0, le=1)
    volume_tolerance: float = Field(default=0.25, gt=0, le=1)
    exclusion_bars: int = Field(default=24, ge=1, le=500)


class HypothesisSpec(BaseModel):
    """Versioned DSL genotype used by upper-level evolution."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: UUID = Field(default_factory=uuid4)
    dsl_version: Literal["event-hypothesis-v1"] = "event-hypothesis-v1"
    lane: HypothesisLane
    lineage_kind: LineageKind = "seed"
    parent_ids: list[UUID] = Field(default_factory=list, max_length=2)
    thesis: str = Field(min_length=20, max_length=2_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    event_types: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    assets: list[str] = Field(default_factory=list, max_length=32)
    applicable_regimes: list[str] = Field(default_factory=list, max_length=16)
    direction: Direction
    trigger_mode: TriggerMode
    confirmation: ConfirmationSpec = Field(default_factory=ConfirmationSpec)
    invalidation: InvalidationSpec = Field(default_factory=InvalidationSpec)
    risk: RiskSpec = Field(default_factory=RiskSpec)
    counterfactual: CounterfactualSpec = Field(default_factory=CounterfactualSpec)
    compiler_version: Literal["event-strategy-compiler-v1"] = "event-strategy-compiler-v1"

    @field_validator("event_types", mode="after")
    @classmethod
    def validate_event_types(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().lower() for item in value if item.strip()})
        unknown = set(normalized) - _EVENT_TYPES
        if unknown:
            raise ValueError(f"unsupported event types: {sorted(unknown)}")
        return normalized

    @field_validator("assets", mode="after")
    @classmethod
    def normalize_assets(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in value if item.strip()})

    @field_validator("evidence_ids", "applicable_regimes", mode="after")
    @classmethod
    def dedupe_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def validate_trigger_safety(self) -> HypothesisSpec:
        if self.trigger_mode == "direct" and not set(self.event_types) <= _DIRECT_ALLOWED:
            raise ValueError("direct trigger is restricted to listing/delisting/exploit/chain_halt")
        if self.lane == "restart" and self.lineage_kind != "restart":
            raise ValueError("restart lane requires lineage_kind='restart'")
        return self


__all__ = [
    "ConfirmationSpec",
    "CounterfactualSpec",
    "HypothesisSpec",
    "InvalidationSpec",
    "RiskSpec",
]

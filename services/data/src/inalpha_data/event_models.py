"""Point-in-time market event contracts owned by the data service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventType = Literal[
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
]
EventSourceTier = Literal["official", "professional_media", "aggregator", "structured"]


def _utc(value: datetime | None) -> datetime | None:
    """Normalize timestamps so hashing and database comparisons are stable."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RawEventIngestRequest(BaseModel):
    """Append one immutable raw-event version, or return the matching version."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=500)
    title: str = Field(default="", max_length=2_000)
    content: str = Field(default="", max_length=200_000)
    url: str | None = Field(default=None, max_length=4_000)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    source_valid_at: datetime | None = None
    claimed_published_at: datetime | None = None
    first_seen_at: datetime
    fetched_at: datetime
    accepted_at: datetime
    collector_version: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(min_length=1, max_length=120)
    source_tier: EventSourceTier
    retracted: bool = False

    @field_validator(
        "source_valid_at",
        "claimed_published_at",
        "first_seen_at",
        "fetched_at",
        "accepted_at",
        mode="after",
    )
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return _utc(value)

    @model_validator(mode="after")
    def validate_observation_order(self) -> RawEventIngestRequest:
        if self.fetched_at < self.first_seen_at:
            raise ValueError("fetched_at cannot be earlier than first_seen_at")
        if self.accepted_at < self.first_seen_at:
            raise ValueError("accepted_at cannot be earlier than first_seen_at")
        return self


class RawEventRecord(BaseModel):
    """Persisted immutable raw-event version."""

    event_id: UUID
    source: str
    source_event_id: str
    version: int
    title: str
    content: str
    url: str | None
    content_hash: str
    raw_payload: dict[str, Any]
    source_valid_at: datetime | None
    claimed_published_at: datetime | None
    first_seen_at: datetime
    fetched_at: datetime
    accepted_at: datetime
    collector_version: str
    policy_version: str
    source_tier: EventSourceTier
    supersedes_event_id: UUID | None
    retracted: bool
    created_at: datetime


class RawEventIngestResponse(BaseModel):
    """Idempotent ingest result."""

    event: RawEventRecord
    created: bool


class EvidenceSpan(BaseModel):
    """Bounded evidence reference; downstream prompts never receive raw content."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bounds(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class EventFactWriteRequest(BaseModel):
    """Append a versioned normalized fact derived from one raw event."""

    model_config = ConfigDict(extra="forbid")

    raw_event_id: UUID
    fact_key: str = Field(min_length=1, max_length=240)
    event_type: EventType
    assets: list[str] = Field(default_factory=list, max_length=64)
    actor: str | None = Field(default=None, max_length=500)
    action: str = Field(min_length=1, max_length=2_000)
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    effective_at: datetime
    available_at: datetime
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list, max_length=32)
    extractor_version: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(min_length=1, max_length=120)
    retracted: bool = False

    @field_validator("effective_at", "available_at", mode="after")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        normalized = _utc(value)
        assert normalized is not None
        return normalized

    @field_validator("assets", mode="after")
    @classmethod
    def normalize_assets(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in value if item.strip()})


class EventFactRecord(BaseModel):
    """One point-in-time normalized event fact version."""

    fact_id: UUID
    raw_event_id: UUID
    fact_key: str
    version: int
    fact_hash: str
    event_type: EventType
    assets: list[str]
    actor: str | None
    action: str
    severity: float
    confidence: float
    effective_at: datetime
    available_at: datetime
    evidence_spans: list[EvidenceSpan]
    extractor_version: str
    policy_version: str
    supersedes_fact_id: UUID | None
    retracted: bool
    created_at: datetime


class EventFactWriteResponse(BaseModel):
    """Idempotent normalized fact write result."""

    fact: EventFactRecord
    created: bool


class EventSnapshotRequest(BaseModel):
    """Freeze all latest visible fact versions at one point in time."""

    model_config = ConfigDict(extra="forbid")

    cutoff: datetime
    policy_version: str = Field(min_length=1, max_length=120)
    event_types: list[EventType] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("cutoff", mode="after")
    @classmethod
    def normalize_cutoff(cls, value: datetime) -> datetime:
        normalized = _utc(value)
        assert normalized is not None
        return normalized

    @field_validator("event_types", mode="after")
    @classmethod
    def normalize_types(cls, value: list[EventType]) -> list[EventType]:
        return sorted(set(value))

    @field_validator("assets", mode="after")
    @classmethod
    def normalize_assets(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in value if item.strip()})


class EventSnapshotRecord(BaseModel):
    """Immutable event set used by research and backtests."""

    snapshot_id: UUID
    cutoff: datetime
    policy_version: str
    query_hash: str
    events_sha256: str
    coverage: dict[str, Any]
    event_types: list[EventType]
    assets: list[str]
    fact_count: int
    created_at: datetime
    facts: list[EventFactRecord] = Field(default_factory=list)


class EventCoverageResponse(BaseModel):
    """Operational coverage summary for Dashboard health views."""

    as_of: datetime
    sources: list[dict[str, Any]]
    raw_event_count: int
    fact_count: int
    retraction_count: int
    latest_accepted_at: datetime | None


class CoinMarketCalImportRequest(BaseModel):
    """Bounded historical import request for the configured Professional API."""

    from_date: datetime
    to_date: datetime
    coins: list[str] = Field(default_factory=list, max_length=100)
    categories: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("from_date", "to_date", mode="after")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        normalized = _utc(value)
        assert normalized is not None
        return normalized

    @model_validator(mode="after")
    def validate_window(self) -> CoinMarketCalImportRequest:
        if self.from_date >= self.to_date:
            raise ValueError("from_date must be earlier than to_date")
        return self


class EventImportResponse(BaseModel):
    """Historical import summary without returning raw provider payloads."""

    source: str
    fetched: int
    created: int
    unchanged: int
    failed: int

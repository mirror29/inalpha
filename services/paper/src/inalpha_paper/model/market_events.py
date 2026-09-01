"""Generic normalized market events consumed by deterministic strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """Point-in-time event fact detached from Research and Evolver service models."""

    event_id: str
    event_type: str
    assets: tuple[str, ...]
    action: str
    severity: float
    confidence: float
    effective_at: int
    available_at: int
    evidence_ids: tuple[str, ...] = ()
    policy_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("MarketEvent.event_id is required")
        if self.available_at < 0 or self.effective_at < 0:
            raise ValueError("MarketEvent timestamps must be non-negative")
        if not 0 <= self.severity <= 1 or not 0 <= self.confidence <= 1:
            raise ValueError("MarketEvent severity/confidence must be within [0,1]")


__all__ = ["MarketEvent"]

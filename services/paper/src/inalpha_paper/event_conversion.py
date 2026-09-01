"""Data EventFact wire payload to Paper MarketEvent conversion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .model.market_events import MarketEvent


def market_event_from_fact(value: dict[str, Any]) -> MarketEvent:
    """Convert without importing Data service models into the Paper kernel."""
    effective_at = datetime.fromisoformat(str(value["effective_at"]).replace("Z", "+00:00"))
    available_at = datetime.fromisoformat(str(value["available_at"]).replace("Z", "+00:00"))
    evidence = value.get("evidence_spans") or []
    return MarketEvent(
        event_id=str(value["fact_id"]),
        event_type=str(value["event_type"]),
        assets=tuple(str(item).upper() for item in value.get("assets") or []),
        action=str(value.get("action") or ""),
        severity=float(value.get("severity") or 0),
        confidence=float(value.get("confidence") or 0),
        effective_at=int(effective_at.timestamp() * 1_000_000_000),
        available_at=int(available_at.timestamp() * 1_000_000_000),
        evidence_ids=tuple(
            str(item.get("quote_hash")) for item in evidence if isinstance(item, dict)
        ),
        policy_version=str(value.get("policy_version") or ""),
        metadata={"raw_event_id": str(value.get("raw_event_id") or "")},
    )


__all__ = ["market_event_from_fact"]

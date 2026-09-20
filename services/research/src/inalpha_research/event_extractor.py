"""Deterministic, tenant-neutral conversion from raw evidence to safe facts."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

_EVENT_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("delisting", ("delist", "remove trading", "下架", "退市"), 0.95),
    ("listing", ("listing", "list on", "new pair", "上线", "上币"), 0.75),
    ("exploit", ("exploit", "hack", "drained", "漏洞", "攻击", "被盗"), 1.0),
    ("chain_halt", ("chain halt", "network halt", "paused chain", "停链", "暂停出块"), 1.0),
    ("regulatory", ("regulator", "sec ", "lawsuit", "ban", "监管", "诉讼"), 0.8),
    ("upgrade", ("upgrade", "mainnet", "hard fork", "升级", "主网"), 0.55),
    ("unlock", ("token unlock", "vesting", "解锁"), 0.65),
    ("burn", ("token burn", "burned", "销毁"), 0.5),
    ("partnership", ("partnership", "integration", "合作", "集成"), 0.4),
    ("macro", ("interest rate", "inflation", "cpi", "利率", "通胀"), 0.55),
)


def extract_event_fact(raw: dict[str, Any], policy_version: str) -> dict[str, Any]:
    """Map curated provider text to one conservative fact with hashed evidence."""
    title = str(raw.get("title") or "")
    content = str(raw.get("content") or "")
    evidence = f"{title}\n{content}".strip()
    lowered = evidence.lower()
    event_type = "other"
    severity = 0.3
    for candidate, terms, candidate_severity in _EVENT_RULES:
        if any(term in lowered for term in terms):
            event_type = candidate
            severity = candidate_severity
            break
    raw_payload = raw.get("raw_payload") if isinstance(raw.get("raw_payload"), dict) else {}
    assets = _assets(raw_payload, evidence)
    effective_at = raw.get("source_valid_at") or raw.get("claimed_published_at")
    effective_at = effective_at or raw["first_seen_at"]
    available_at = raw["accepted_at"]
    end = min(len(evidence), 2_000)
    spans = []
    if end:
        spans.append(
            {
                "start": 0,
                "end": end,
                "quote_hash": hashlib.sha256(evidence[:end].encode()).hexdigest(),
            }
        )
    fact_key = hashlib.sha256(
        f"{raw['source']}\0{raw['source_event_id']}\0{event_type}".encode()
    ).hexdigest()[:48]
    return {
        "raw_event_id": raw["event_id"],
        "fact_key": fact_key,
        "event_type": event_type,
        "assets": assets,
        "actor": str(raw_payload.get("exchange") or raw.get("source") or "")[:500] or None,
        "action": title[:2_000] or f"{event_type} event",
        "severity": severity,
        "confidence": 0.85 if event_type != "other" else 0.35,
        "effective_at": _iso(effective_at),
        "available_at": _iso(available_at),
        "evidence_spans": spans,
        "extractor_version": "deterministic-event-extractor-v1",
        "policy_version": policy_version,
        "retracted": bool(raw.get("retracted")),
    }


def _assets(payload: dict[str, Any], evidence: str) -> list[str]:
    """Extract normalized crypto symbols from structured fields, then bounded text."""
    values: list[str] = []
    for key in ("coins", "symbols", "coin", "symbol"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    values.append(str(item.get("symbol") or item.get("code") or ""))
                else:
                    values.append(str(item))
        elif value:
            values.append(str(value))
    values.extend(
        re.findall(r"\b(?:BTC|ETH|SOL|XRP|ADA|DOGE|BNB|AVAX|DOT|LINK)\b", evidence.upper())
    )
    return sorted({item.strip().upper().split("/")[0] for item in values if item.strip()})[:64]


def _iso(value: Any) -> str:
    """Return one UTC ISO timestamp accepted by Data's strict schema."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


__all__ = ["extract_event_fact"]

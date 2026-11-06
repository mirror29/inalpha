"""Regulatory keywords must not classify unrelated words as prohibition events."""

from __future__ import annotations

import pytest

from inalpha_research.event_extractor import extract_event_fact


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Bitcoin bank expands custody", "other"),
        ("Ethereum banking products grow", "other"),
        ("Solana urban art project launches", "other"),
        ("Bitcoin bankruptcy proceedings continue", "other"),
        ("Bitcoin ban announced", "regulatory"),
        ("Bitcoin banned by government", "regulatory"),
        ("Banning Bitcoin payments", "regulatory"),
        ("Government bans Bitcoin payments", "regulatory"),
        ("Bitcoin: BAN!", "regulatory"),
        ("Bank faces Bitcoin lawsuit", "regulatory"),
        ("比特币监管政策", "regulatory"),
        ("中国拟ban加密货币交易", "regulatory"),
        ("比特币banned交易政策", "regulatory"),
        ("Bancor token activity", "other"),
        ("Bangladesh Bitcoin activity", "other"),
        ("Bitcoin exploit followed by ban", "exploit"),
    ],
)
def test_ban_word_boundary(title: str, expected: str) -> None:
    raw = {
        "event_id": "11111111-1111-4111-8111-111111111111",
        "source": "test",
        "source_event_id": "boundary-case",
        "title": title,
        "content": "",
        "first_seen_at": "2026-10-13T00:00:00Z",
        "accepted_at": "2026-10-13T00:00:01Z",
    }
    fact = extract_event_fact(raw, "first-seen-only-v1")
    assert fact["event_type"] == expected
    assert fact["extractor_version"] == "deterministic-event-extractor-v3"
    assert fact["available_at"] == "2026-10-13T00:00:01+00:00"
    assert fact["effective_at"] == "2026-10-13T00:00:00+00:00"

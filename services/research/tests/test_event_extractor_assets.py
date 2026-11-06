"""Asset attribution must handle explicit names without turning ordinary prose into symbols."""

from __future__ import annotations

import pytest

from inalpha_research.event_extractor import EXTRACTOR_VERSION, _assets, extract_event_fact


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Bitcoin rally cools; Ethereum and Solana remain active", ["BTC", "ETH", "SOL"]),
        ("bitcoin and BITCOIN with BTC", ["BTC"]),
        ("Cardano, Dogecoin, Polkadot, Chainlink and Binance Coin", ["ADA", "BNB", "DOGE", "DOT", "LINK"]),
        ("Read this link and connect the dot. Consolidation continues.", []),
        ("Bitcoin Cash and Ethereum Classic launch updates", []),
        ("Bitcoin SV and Bitcoin Gold are different assets", []),
        ("Bitcoin-Cash and Ethereum-Classic launch updates", []),
        ("Bitcoin Cash delisting: https://example.com/bitcoin-cash", []),
        ("BTC and LINK remain volatile", ["BTC", "LINK"]),
        ("Ripple announces a partnership. An avalanche blocks a road.", []),
        ("Notbitcoin bitcoinish ethereums", []),
    ],
)
def test_explicit_asset_mentions(text: str, expected: list[str]) -> None:
    assert _assets({}, text) == expected


def test_structured_symbols_remain_authoritative_and_deduplicated() -> None:
    assert _assets({"symbols": ["btc", "ETH/USDT", {"code": "SOL"}]}, "Bitcoin") == ["BTC", "ETH", "SOL"]


def test_asset_fix_preserves_evidence_and_availability_boundary() -> None:
    raw = {
        "event_id": "11111111-1111-4111-8111-111111111111",
        "source": "coindesk",
        "source_event_id": "archived-news-1",
        "title": "Bitcoin exchange exploit",
        "content": "",
        "accepted_at": "2026-09-30T06:00:00Z",
        "first_seen_at": "2026-09-30T06:00:00Z",
        "claimed_published_at": "2026-09-29T00:00:00Z",
    }
    first = extract_event_fact(raw, "first-seen-only-v1")
    assert first == extract_event_fact(raw, "first-seen-only-v1")
    assert first["assets"] == ["BTC"]
    assert first["event_type"] == "exploit"
    assert first["available_at"] == "2026-09-30T06:00:00+00:00"
    assert first["effective_at"] == "2026-09-29T00:00:00+00:00"
    assert first["extractor_version"] == EXTRACTOR_VERSION

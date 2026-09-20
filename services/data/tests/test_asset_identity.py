"""Authoritative asset identity resolution tests."""

import pytest

from inalpha_data.asset_identity import resolve_asset_identity


@pytest.mark.parametrize(
    ("venue", "symbol", "asset_id", "event_code"),
    [
        ("binance", "BTC/USDT:USDT", "asset:BTC", "BTC"),
        ("binance", "ETHUSDT", "asset:ETH", "ETH"),
        ("akshare", "600519.SH", "asset:SH.600519", "SH.600519"),
        ("yfinance", "AAPL", "asset:AAPL", "AAPL"),
    ],
)
def test_resolve_asset_identity_is_stable_and_market_aware(
    venue: str,
    symbol: str,
    asset_id: str,
    event_code: str,
) -> None:
    identity = resolve_asset_identity(venue, symbol)

    assert identity.asset_id == asset_id
    assert identity.event_asset_code == event_code

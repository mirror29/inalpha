"""``BinanceConnector.fetch_perp_funding_rate`` 单测(mock futures ccxt,零网络)。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from inalpha_data.connectors.binance import BinanceConnector


class _FakeFutures:
    """假 ccxt futures 实例:fetch_funding_rate 返固定 payload。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.closed = False

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        return self._payload

    async def close(self) -> None:
        self.closed = True


async def test_fetch_perp_funding_rate_parses() -> None:
    conn = BinanceConnector()
    conn._futures_exchange = _FakeFutures(  # type: ignore[assignment]
        {
            "symbol": "BTC/USDT:USDT",
            "markPrice": 60000.5,
            "fundingRate": 0.0001,
            "timestamp": 1_700_000_000_000,
            "fundingTimestamp": 1_700_028_800_000,
        }
    )
    out = await conn.fetch_perp_funding_rate("BTC/USDT:USDT")
    assert out["symbol"] == "BTC/USDT:USDT"
    assert out["mark_price"] == 60000.5
    assert out["funding_rate"] == 0.0001
    assert out["ts"] == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert out["next_funding_ts"] == datetime.fromtimestamp(1_700_028_800, tz=UTC)


async def test_fetch_perp_funding_rate_missing_fields_raises() -> None:
    conn = BinanceConnector()
    conn._futures_exchange = _FakeFutures({"symbol": "BTC/USDT:USDT"})  # type: ignore[assignment]
    with pytest.raises(ValueError):
        await conn.fetch_perp_funding_rate("BTC/USDT:USDT")


async def test_fetch_perp_funding_rate_no_timestamp_falls_back_to_now() -> None:
    conn = BinanceConnector()
    conn._futures_exchange = _FakeFutures(  # type: ignore[assignment]
        {"markPrice": 100.0, "fundingRate": -0.0002}  # 无 timestamp/fundingTimestamp
    )
    out = await conn.fetch_perp_funding_rate("ETH/USDT:USDT")
    assert out["funding_rate"] == -0.0002  # 负费率(空头付多头)
    assert out["ts"] is not None  # now() 兜底
    assert out["next_funding_ts"] is None

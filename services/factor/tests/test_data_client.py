"""factor DataClient 单测 —— fresh=True 触发 backfill（金融时效性 §3.1，CR fix）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from inalpha_factor.data_client import DataClient


def _bar(ts: str) -> dict:
    return {
        "ts": ts, "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h",
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
    }


@respx.mock
async def test_fresh_true_triggers_backfill_then_bars() -> None:
    backfill = respx.post("http://data.test/backfill/bars").mock(
        return_value=httpx.Response(200, json={"bars_inserted": 1})
    )
    bars = respx.get("http://data.test/bars").mock(
        return_value=httpx.Response(200, json=[_bar("2026-06-05T00:00:00Z")])
    )
    now = datetime(2026, 6, 5, tzinfo=UTC)
    async with DataClient("http://data.test", "t") as dc:
        out = await dc.get_bars(
            venue="binance", symbol="BTC/USDT", timeframe="1h",
            from_ts=now - timedelta(days=30), to_ts=now, fresh=True,
        )
    assert backfill.called  # fresh=True 先 backfill
    assert bars.called
    assert len(out) == 1


@respx.mock
async def test_fresh_false_skips_backfill() -> None:
    backfill = respx.post("http://data.test/backfill/bars").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("http://data.test/bars").mock(
        return_value=httpx.Response(200, json=[_bar("2026-06-05T00:00:00Z")])
    )
    now = datetime(2026, 6, 5, tzinfo=UTC)
    async with DataClient("http://data.test", "t") as dc:
        await dc.get_bars(
            venue="binance", symbol="BTC/USDT", timeframe="1h",
            from_ts=now - timedelta(days=30), to_ts=now,  # fresh 默认 False
        )
    assert not backfill.called  # 历史语义不补


@respx.mock
async def test_backfill_failure_is_silent() -> None:
    respx.post("http://data.test/backfill/bars").mock(side_effect=httpx.ConnectError("boom"))
    bars = respx.get("http://data.test/bars").mock(
        return_value=httpx.Response(200, json=[_bar("2026-06-05T00:00:00Z")])
    )
    now = datetime(2026, 6, 5, tzinfo=UTC)
    async with DataClient("http://data.test", "t") as dc:
        out = await dc.get_bars(
            venue="binance", symbol="BTC/USDT", timeframe="1h",
            from_ts=now - timedelta(days=30), to_ts=now, fresh=True,
        )
    assert bars.called  # backfill 失败不抛，退化到读 DB 缓存
    assert len(out) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

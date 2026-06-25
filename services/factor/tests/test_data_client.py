"""factor DataClient 单测 —— fresh=True 触发 backfill（金融时效性 §3.1，CR fix）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from inalpha_factor.data_client import DataClient, DataServiceError


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


@respx.mock
async def test_get_bars_retries_transient_then_succeeds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连接级瞬时失败（data 重启窗口 / 并发突发）有界重试后成功，不整条失败。"""
    monkeypatch.setattr("inalpha_factor.data_client._GET_BACKOFF_S", (0.0, 0.0))
    route = respx.get("http://data.test/bars").mock(
        side_effect=[
            httpx.ConnectError("blip"),
            httpx.ConnectError("blip"),
            httpx.Response(200, json=[_bar("2026-06-05T00:00:00Z")]),
        ]
    )
    now = datetime(2026, 6, 5, tzinfo=UTC)
    async with DataClient("http://data.test", "t") as dc:
        out = await dc.get_bars(
            venue="binance", symbol="BTC/USDT", timeframe="1h",
            from_ts=now - timedelta(days=30), to_ts=now,
        )
    assert route.call_count == 3  # 失败 2 次 + 成功 1 次
    assert len(out) == 1


@respx.mock
async def test_get_bars_raises_after_exhausting_retries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """连接持续失败（data 真挂）耗尽重试后抛 DATA_SERVICE_UNREACHABLE，不静默。"""
    monkeypatch.setattr("inalpha_factor.data_client._GET_BACKOFF_S", (0.0, 0.0))
    route = respx.get("http://data.test/bars").mock(side_effect=httpx.ConnectError("down"))
    now = datetime(2026, 6, 5, tzinfo=UTC)
    async with DataClient("http://data.test", "t") as dc:
        with pytest.raises(DataServiceError) as ei:
            await dc.get_bars(
                venue="binance", symbol="BTC/USDT", timeframe="1h",
                from_ts=now - timedelta(days=30), to_ts=now,
            )
    assert ei.value.code == "DATA_SERVICE_UNREACHABLE"
    assert route.call_count == 3  # 重试上限


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

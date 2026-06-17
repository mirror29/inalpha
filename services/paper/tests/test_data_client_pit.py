"""``DataClient.get_bars_pit`` 单测（ADR-0053 阶段 A）—— PIT 截断过滤未来 bar。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from inalpha_paper.data_client import DataClient

pytestmark = pytest.mark.anyio


def _bar(ts: str) -> dict[str, Any]:
    return {"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}


async def test_get_bars_pit_drops_bars_after_as_of() -> None:
    dc = DataClient("http://data.test", "tok")
    all_bars = [
        _bar("2026-01-01T00:00:00Z"),
        _bar("2026-01-05T00:00:00Z"),
        _bar("2026-01-10T00:00:00Z"),  # as_of 之后 → 应被丢
    ]
    captured: dict[str, Any] = {}

    async def fake_get_bars(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return all_bars

    dc.get_bars = fake_get_bars  # type: ignore[method-assign]
    out = await dc.get_bars_pit(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1d",
        from_ts=datetime(2026, 1, 1, tzinfo=UTC),
        as_of=datetime(2026, 1, 6, tzinfo=UTC),
    )
    # 只保留 ts <= as_of 的两根
    assert [b["ts"] for b in out] == ["2026-01-01T00:00:00Z", "2026-01-05T00:00:00Z"]
    # PIT 历史重建：to_ts 钉在 as_of、fresh=False
    assert captured["to_ts"] == datetime(2026, 1, 6, tzinfo=UTC)
    assert captured["fresh"] is False


async def test_get_bars_pit_applies_publish_lag() -> None:
    dc = DataClient("http://data.test", "tok")
    all_bars = [_bar("2026-01-01T00:00:00Z"), _bar("2026-01-05T00:00:00Z")]

    async def fake_get_bars(**kwargs: Any) -> list[dict[str, Any]]:
        return all_bars

    dc.get_bars = fake_get_bars  # type: ignore[method-assign]
    # as_of=01-06 但 publish_lag=3d → cutoff=01-03，只剩 01-01 那根
    out = await dc.get_bars_pit(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1d",
        from_ts=datetime(2026, 1, 1, tzinfo=UTC),
        as_of=datetime(2026, 1, 6, tzinfo=UTC),
        publish_lag=timedelta(days=3),
    )
    assert [b["ts"] for b in out] == ["2026-01-01T00:00:00Z"]

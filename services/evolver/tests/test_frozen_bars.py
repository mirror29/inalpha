"""严格冻结行情加载器测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from inalpha_shared.errors import ValidationError

from inalpha_evolver.data.frozen_bars import FrozenBarsLoader

_AS_OF = datetime(2026, 8, 12, 12, tzinfo=UTC)


class FakeDataClient:
    def __init__(
        self,
        bars: list[dict[str, Any]],
        *,
        backfill_error: Exception | None = None,
    ) -> None:
        self.bars = bars
        self.backfill_error = backfill_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def backfill_bars(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("backfill", kwargs))
        if self.backfill_error:
            raise self.backfill_error
        return {
            "venue": kwargs["venue"],
            "symbol": kwargs["symbol"],
            "timeframe": kwargs["timeframe"],
            "bars_fetched": len(self.bars),
            "bars_inserted": len(self.bars),
            "from_ts": kwargs["from_ts"],
            "to_ts": kwargs["to_ts"],
        }

    async def get_bars(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("get", kwargs))
        return self.bars


def _hourly_bars(count: int = 3, *, end: datetime = _AS_OF) -> list[dict[str, Any]]:
    start = end - timedelta(hours=count)
    return [
        {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "ts": (start + timedelta(hours=index)).isoformat(),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10.0,
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_load_backfills_then_reads_without_fresh_fallback() -> None:
    client = FakeDataClient(_hourly_bars())
    dataset = await FrozenBarsLoader(client).load(  # type: ignore[arg-type]
        venue="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        from_ts=_AS_OF - timedelta(hours=3),
        as_of=_AS_OF,
    )

    assert [call[0] for call in client.calls] == ["backfill", "get"]
    assert client.calls[1][1]["fresh"] is False
    assert client.calls[1][1]["limit"] == 10_001
    assert dataset.manifest.bar_count == 3
    assert dataset.manifest.latest_bar_ts == _AS_OF - timedelta(hours=1)
    assert dataset.manifest.cutoff_bar_ts == dataset.manifest.latest_bar_ts
    assert dataset.manifest.data_epoch == int(dataset.manifest.latest_bar_ts.timestamp() * 1000)
    assert dataset.manifest.backfill.bars_fetched == 3
    assert dataset.manifest.backfill.venue == "binance"
    assert len(dataset.manifest.content_sha256) == 64


@pytest.mark.asyncio
async def test_forming_bar_is_removed() -> None:
    bars = _hourly_bars()
    bars.append({**bars[-1], "ts": _AS_OF.isoformat()})
    client = FakeDataClient(bars)
    dataset = await FrozenBarsLoader(client).load(  # type: ignore[arg-type]
        venue="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        from_ts=_AS_OF - timedelta(hours=3),
        as_of=_AS_OF,
    )
    assert dataset.manifest.bar_count == 3


@pytest.mark.asyncio
async def test_stale_tail_fails_closed() -> None:
    client = FakeDataClient(_hourly_bars(end=_AS_OF - timedelta(hours=1)))
    with pytest.raises(ValidationError) as error:
        await FrozenBarsLoader(client).load(  # type: ignore[arg-type]
            venue="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            from_ts=_AS_OF - timedelta(hours=3),
            as_of=_AS_OF,
        )
    assert error.value.code == "EVOLUTION_DATA_FRESHNESS_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["duplicate", "nan", "bad_ohlc"])
async def test_bad_bars_fail_closed(mutation: str) -> None:
    bars = _hourly_bars()
    if mutation == "duplicate":
        bars[1]["ts"] = bars[0]["ts"]
    elif mutation == "nan":
        bars[1]["close"] = float("nan")
    else:
        bars[1]["high"] = 50.0
    client = FakeDataClient(bars)
    with pytest.raises(ValidationError):
        await FrozenBarsLoader(client).load(  # type: ignore[arg-type]
            venue="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            from_ts=_AS_OF - timedelta(hours=3),
            as_of=_AS_OF,
        )


@pytest.mark.asyncio
async def test_middle_gap_fails_even_when_tail_is_fresh() -> None:
    bars = _hourly_bars(4)
    del bars[1]
    with pytest.raises(ValidationError) as error:
        await FrozenBarsLoader(FakeDataClient(bars)).load(  # type: ignore[arg-type]
            venue="binance", symbol="BTCUSDT", timeframe="1h",
            from_ts=_AS_OF - timedelta(hours=4), as_of=_AS_OF)
    assert error.value.code == "EVOLUTION_DATA_GAP_INVALID"


@pytest.mark.asyncio
async def test_equity_holiday_is_not_reported_as_gap() -> None:
    as_of = datetime(2026, 7, 6, 21, tzinfo=UTC)
    bars = [
        {
            "venue": "yfinance", "symbol": "AAPL", "timeframe": "1d",
            "ts": datetime(2026, 7, day, 4, tzinfo=UTC).isoformat(),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 10.0,
        }
        for day in (1, 2, 6)
    ]
    dataset = await FrozenBarsLoader(FakeDataClient(bars)).load(  # type: ignore[arg-type]
        venue="yfinance", symbol="AAPL", timeframe="1d",
        from_ts=datetime(2026, 7, 1, tzinfo=UTC), as_of=as_of)
    assert dataset.manifest.bar_count == 3


@pytest.mark.asyncio
async def test_loader_rejects_naive_and_future_as_of() -> None:
    loader = FrozenBarsLoader(FakeDataClient(_hourly_bars()))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as naive:
        await loader.load(venue="binance", symbol="BTCUSDT", timeframe="1h",
                          from_ts=_AS_OF - timedelta(hours=3),
                          as_of=_AS_OF.replace(tzinfo=None))
    assert naive.value.code == "EVOLUTION_DATETIME_NAIVE"

    future = datetime.now(UTC) + timedelta(minutes=1)
    with pytest.raises(ValidationError) as ahead:
        await loader.load(venue="binance", symbol="BTCUSDT", timeframe="1h",
                          from_ts=_AS_OF, as_of=future)
    assert ahead.value.code == "EVOLUTION_AS_OF_IN_FUTURE"


@pytest.mark.asyncio
async def test_missing_window_prefix_is_rejected_even_with_continuous_fresh_tail() -> None:
    with pytest.raises(ValidationError) as error:
        await FrozenBarsLoader(FakeDataClient(_hourly_bars())).load(  # type: ignore[arg-type]
            venue="binance", symbol="BTCUSDT", timeframe="1h",
            from_ts=_AS_OF - timedelta(days=1), as_of=_AS_OF)
    assert error.value.code == "EVOLUTION_DATA_GAP_INVALID"
    assert error.value.details["missing_count"] == 21
    assert len(error.value.details["missing"]) == 20

"""冻结行情加载错误与哈希测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from inalpha_paper.data_client import DataServiceError
from inalpha_shared.errors import ValidationError

from inalpha_evolver.data.frozen_bars import FrozenBarsLoader

_AS_OF = datetime(2026, 8, 12, 12, tzinfo=UTC)


class FakeDataClient:
    def __init__(
        self,
        bars: list[dict[str, Any]],
        error: Exception | None = None,
    ) -> None:
        self.bars = bars
        self.error = error

    async def backfill_bars(self, **kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise self.error
        return {
            "venue": kwargs["venue"],
            "symbol": kwargs["symbol"],
            "timeframe": kwargs["timeframe"],
            "bars_fetched": len(self.bars),
            "bars_inserted": len(self.bars),
            "from_ts": kwargs["from_ts"],
            "to_ts": kwargs["to_ts"],
        }

    async def get_bars(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return self.bars


def _bars() -> list[dict[str, Any]]:
    return [
        {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "ts": (_AS_OF - timedelta(hours=3 - index)).isoformat(),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10.0,
        }
        for index in range(3)
    ]


async def _load(client: FakeDataClient):
    return await FrozenBarsLoader(client).load(  # type: ignore[arg-type]
        venue="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        from_ts=_AS_OF - timedelta(hours=3),
        as_of=_AS_OF,
    )


@pytest.mark.asyncio
async def test_backfill_failure_never_reads_cached_bars() -> None:
    client = FakeDataClient(
        _bars(),
        DataServiceError("failed", code="DATA_BACKFILL_FAILED"),
    )
    with pytest.raises(ValidationError) as error:
        await _load(client)
    assert error.value.code == "EVOLUTION_DATA_FRESHNESS_FAILED"


@pytest.mark.asyncio
async def test_dataset_limit_is_rejected() -> None:
    client = FakeDataClient(_bars() * 3334)
    with pytest.raises(ValidationError) as error:
        await _load(client)
    assert error.value.code == "EVOLUTION_DATA_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_hash_is_stable_and_sensitive_to_ohlcv() -> None:
    first = await _load(FakeDataClient(_bars()))
    second = await _load(FakeDataClient(_bars()))
    changed = _bars()
    changed[0]["volume"] = 11.0
    third = await _load(FakeDataClient(changed))

    assert first.manifest.content_sha256 == second.manifest.content_sha256
    assert first.manifest.content_sha256 != third.manifest.content_sha256


@pytest.mark.asyncio
async def test_identity_mismatch_is_rejected() -> None:
    bars = _bars()
    bars[0]["symbol"] = "ETHUSDT"
    with pytest.raises(ValidationError) as error:
        await _load(FakeDataClient(bars))
    assert error.value.code == "EVOLUTION_DATA_IDENTITY_MISMATCH"

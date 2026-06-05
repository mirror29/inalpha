"""engine.score 的 live → fresh 判定（CR medium fix）。

捕获传给 _fetch_df 的 fresh 标记：as_of=None 或近 now → fresh=True；显式较早 as_of → False。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from inalpha_factor.config import get_factor_settings
from inalpha_factor.engine import FactorEngine

from .conftest import make_ohlcv


class _CaptureEngine(FactorEngine):
    def __init__(self) -> None:
        super().__init__(get_factor_settings())
        self.fresh_arg: bool | None = None

    async def _fetch_df(self, *, fresh: bool = False, **_kw: object) -> pd.DataFrame:  # type: ignore[override]
        self.fresh_arg = fresh
        return make_ohlcv(400)


async def _score(eng: _CaptureEngine, as_of: datetime | None) -> None:
    await eng.score(
        venue="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of,
        lookback_bars=300, horizon_bars=5, quantiles=5, factor_ids=None,
    )


async def test_as_of_none_is_live_fresh() -> None:
    eng = _CaptureEngine()
    await _score(eng, None)
    assert eng.fresh_arg is True


async def test_as_of_recent_is_live_fresh() -> None:
    eng = _CaptureEngine()
    await _score(eng, datetime.now(UTC))  # "当前时刻"（research analyst 传的就是这个）
    assert eng.fresh_arg is True


async def test_as_of_old_is_historical_not_fresh() -> None:
    eng = _CaptureEngine()
    await _score(eng, datetime.now(UTC) - timedelta(days=30))  # 历史分析
    assert eng.fresh_arg is False

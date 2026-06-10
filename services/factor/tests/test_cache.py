"""因子面板缓存单测：live 命中 / 历史 as_of 绕过 / TTL=0 关闭 / 空 df 不缓存。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from inalpha_factor.config import get_factor_settings
from inalpha_factor.engine import FactorEngine

from .conftest import make_ohlcv


class _CountingEngine(FactorEngine):
    """记录 _fetch_df 调用次数的 fake engine。"""

    def __init__(self, df: pd.DataFrame, *, cache_ttl_s: int | None = None) -> None:
        settings = get_factor_settings()
        if cache_ttl_s is not None:
            settings = settings.model_copy(update={"cache_ttl_s": cache_ttl_s})
        super().__init__(settings)
        self._df = df
        self.fetch_count = 0

    async def _fetch_df(self, **_kwargs: object) -> pd.DataFrame:  # type: ignore[override]
        self.fetch_count += 1
        return self._df


def _score_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "as_of": None,
        "lookback_bars": 300,
        "horizon_bars": 5,
        "quantiles": 5,
        "factor_ids": None,
    }
    base.update(overrides)
    return base


async def test_live_score_hits_cache() -> None:
    eng = _CountingEngine(make_ohlcv(400))
    r1 = await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    r2 = await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    assert eng.fetch_count == 1
    assert len(r1["factors"]) == len(r2["factors"]) > 0


async def test_snapshot_reuses_score_cache() -> None:
    """factor.timing 紧跟 score 的常见序列：同 key 只取一次数。"""
    eng = _CountingEngine(make_ohlcv(400))
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    snap = await eng.snapshot(
        venue="binance", symbol="BTC/USDT", timeframe="1h",
        as_of=None, lookback_bars=300, horizon_bars=5, top_n=5,
    )
    assert eng.fetch_count == 1
    assert snap["available"] is True


async def test_historical_as_of_bypasses_cache() -> None:
    """显式较早 as_of = 历史分析，决定性但低频，不走缓存。"""
    eng = _CountingEngine(make_ohlcv(400))
    old = datetime.now(UTC) - timedelta(days=30)
    await eng.score(**_score_kwargs(as_of=old))  # type: ignore[arg-type]
    await eng.score(**_score_kwargs(as_of=old))  # type: ignore[arg-type]
    assert eng.fetch_count == 2


async def test_ttl_zero_disables_cache() -> None:
    eng = _CountingEngine(make_ohlcv(400), cache_ttl_s=0)
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    assert eng.fetch_count == 2


async def test_empty_df_not_cached() -> None:
    """data 抖一下返回空，不能把空结果缓存住毒后续请求。"""
    eng = _CountingEngine(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    assert eng.fetch_count == 2


async def test_different_params_different_keys() -> None:
    """symbol / lookback 不同 = 不同 key，互不命中。"""
    eng = _CountingEngine(make_ohlcv(400))
    await eng.score(**_score_kwargs())  # type: ignore[arg-type]
    await eng.score(**_score_kwargs(symbol="ETH/USDT"))  # type: ignore[arg-type]
    await eng.score(**_score_kwargs(lookback_bars=500))  # type: ignore[arg-type]
    assert eng.fetch_count == 3


async def test_macro_series_cache_hit_returns_close() -> None:
    """macro 缓存**命中路径**返回 df["close"] —— 回归:此前命中时取错下标
    (cached[1] 是恒空的 series dict)KeyError 被上层兜底吃掉,宏观因子自
    第二次请求起静默消失(PR #70 review round2 major)。"""
    eng = _CountingEngine(make_ohlcv(60))
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "from_ts": now - timedelta(days=30),
        "to_ts": now,
        "fresh": True,
    }
    s1 = await eng._fetch_macro_series("DGS10", **kwargs)  # type: ignore[arg-type]
    s2 = await eng._fetch_macro_series("DGS10", **kwargs)  # type: ignore[arg-type]
    assert eng.fetch_count == 1  # 第二次必须走缓存,不再打 data-service
    pd.testing.assert_series_equal(s1, s2)

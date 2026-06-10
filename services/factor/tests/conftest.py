"""factor service 测试 fixture。

factor 服务不连 DB，但基础 ``Settings`` 仍要求 DATABASE_URL / JWT_SECRET 就位
（pydantic 必填字段）。这里塞占位值即可，测试不真连库。
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _clear_panel_cache() -> None:
    """每个测试前清因子面板缓存，避免跨测试串数据（缓存是模块级的）。"""
    from inalpha_factor.engine import _panel_cache

    _panel_cache.clear()


@pytest.fixture(scope="session", autouse=True)
def _ensure_env() -> None:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://quant:devpass@localhost:5433/inalpha"
    )
    os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
    from inalpha_shared.config import get_settings

    from inalpha_factor.config import get_factor_settings

    get_settings.cache_clear()
    get_factor_settings.cache_clear()


def make_ohlcv(n: int = 320, *, seed: int = 7, trend: float = 0.0005) -> pd.DataFrame:
    """合成 OHLCV：带趋势 + 噪声的几何随机游走，index 为 1h tz-aware ts。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.01, size=n)
    close = 100.0 * np.exp(np.cumsum(rets))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.004, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.004, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.uniform(800.0, 1200.0, size=n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()

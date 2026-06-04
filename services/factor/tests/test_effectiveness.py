"""有效性打分单测：合成因子 → 预期 IC 方向。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from inalpha_factor.effectiveness import score_factor


def _close(n: int = 400) -> pd.Series:
    rng = np.random.default_rng(3)
    rets = rng.normal(0.0, 0.01, size=n)
    return pd.Series(100.0 * np.exp(np.cumsum(rets)))


def test_perfect_factor_has_high_positive_ic() -> None:
    """因子 = 真·前瞻收益（lookahead）→ Rank IC 接近 +1、方向 +1。"""
    close = _close()
    horizon = 5
    fwd = close.shift(-horizon) / close - 1.0
    res = score_factor(fwd, close, horizon=horizon, quantiles=5, min_samples=50)
    assert res.rank_ic > 0.95
    assert res.direction == 1
    assert not res.low_confidence
    assert res.strength == 1.0


def test_inverted_factor_has_negative_direction() -> None:
    close = _close()
    horizon = 5
    fwd = close.shift(-horizon) / close - 1.0
    res = score_factor(-fwd, close, horizon=horizon, quantiles=5, min_samples=50)
    assert res.rank_ic < -0.95
    assert res.direction == -1


def test_noise_factor_is_near_zero_ic() -> None:
    close = _close()
    rng = np.random.default_rng(99)
    noise = pd.Series(rng.normal(0.0, 1.0, size=len(close)), index=close.index)
    res = score_factor(noise, close, horizon=5, quantiles=5, min_samples=50)
    assert abs(res.rank_ic) < 0.2


def test_small_sample_flags_low_confidence() -> None:
    close = _close(60)
    fwd = close.shift(-5) / close - 1.0
    res = score_factor(fwd, close, horizon=5, quantiles=5, min_samples=120)
    assert res.low_confidence
    assert res.direction == 0  # 低置信不给方向


def test_latest_value_is_last_non_nan() -> None:
    close = _close(200)
    factor = pd.Series(np.arange(200, dtype=float), index=close.index)
    factor.iloc[-3:] = np.nan
    res = score_factor(factor, close, horizon=5, quantiles=5, min_samples=50)
    assert res.value == 196.0  # 最后一个非 NaN（index 196）

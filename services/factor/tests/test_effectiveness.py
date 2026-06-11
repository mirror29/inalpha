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


def test_recent_ic_tracks_full_ic_for_stable_factor() -> None:
    """全程有效的因子：近期 IC 与全样本 IC 同号且都高（ADR-0043 D4）。"""
    close = _close()
    horizon = 5
    fwd = close.shift(-horizon) / close - 1.0
    res = score_factor(fwd, close, horizon=horizon, quantiles=5, min_samples=50)
    assert res.rank_ic_recent > 0.9


def test_recent_ic_detects_decay() -> None:
    """前 2/3 有效、后 1/3 变纯噪声的因子：全样本 IC 仍正，近期 IC 趋零。"""
    close = _close(600)
    horizon = 5
    fwd = close.shift(-horizon) / close - 1.0
    rng = np.random.default_rng(11)
    factor = fwd.copy()
    cut = 400
    factor.iloc[cut:] = rng.normal(0.0, 1.0, size=len(factor) - cut)
    res = score_factor(factor, close, horizon=horizon, quantiles=5, min_samples=50)
    assert res.rank_ic > 0.3  # 全样本仍被前段拉高
    assert abs(res.rank_ic_recent) < 0.2  # 近期窗暴露衰减


def test_turnover_low_for_slow_factor_high_for_noise() -> None:
    """单调慢变因子换手≈0；纯噪声因子换手≈1。"""
    close = _close()
    slow = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    rng = np.random.default_rng(42)
    noise = pd.Series(rng.normal(0.0, 1.0, size=len(close)), index=close.index)
    res_slow = score_factor(slow, close, horizon=5, quantiles=5, min_samples=50)
    res_noise = score_factor(noise, close, horizon=5, quantiles=5, min_samples=50)
    assert res_slow.turnover < 0.05
    assert res_noise.turnover > 0.8


# ────────────────────────────────────────────────────────────────────
# 衰减三态判定（ADR-0047 D2 —— 从前端 decayState() 下沉的单一权威）
# ────────────────────────────────────────────────────────────────────


def test_decay_state_three_states() -> None:
    from inalpha_factor.effectiveness import decay_state

    # 与前端原 decayState() 三态边界一致
    assert decay_state(0.10, 0.08) == "stable"  # 保住 80%
    assert decay_state(0.10, 0.06) == "stable"  # 恰好 60% 边界归 stable
    assert decay_state(0.10, 0.05) == "fading"  # 保住 50%
    assert decay_state(0.10, -0.05) == "decaying"  # 反号
    assert decay_state(0.10, 0.0) == "decaying"  # 趋零
    assert decay_state(-0.10, -0.08) == "stable"  # 负向因子同理
    assert decay_state(-0.10, 0.05) == "decaying"
    assert decay_state(0.0, 0.05) == "decaying"  # 退化对：全样本 0、近期非 0


def test_score_factor_carries_decay_state() -> None:
    """score_factor 输出带 decay_state，且衰减构造（前 2/3 有效后 1/3 噪声）判 decaying/fading。"""
    rng = np.random.default_rng(7)
    n = 300
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    fwd = close.shift(-5) / close - 1.0
    factor = fwd.copy()
    third = n // 3
    factor.iloc[-third:] = rng.normal(0, 1, third)  # 尾段变纯噪声
    res = score_factor(factor, close, horizon=5, quantiles=5, min_samples=50)
    assert res.decay_state in ("decaying", "fading")

    perfect = score_factor(fwd.fillna(0), close, horizon=5, quantiles=5, min_samples=50)
    assert perfect.decay_state == "stable"


# ────────────────────────────────────────────────────────────────────
# null IC 基准（选择效应地板，ADR-0043 D4 延伸）
# ────────────────────────────────────────────────────────────────────


def test_null_ic_benchmark_monotonicity() -> None:
    from inalpha_factor.effectiveness import null_ic_benchmark

    # 候选越多 → 纯噪声里挑出的最大 |IC| 期望越高
    assert null_ic_benchmark(50, 720, 5) > null_ic_benchmark(10, 720, 5)
    # 样本越多 → σ 越小 → 基准越低
    assert null_ic_benchmark(50, 720, 5) < null_ic_benchmark(50, 240, 5)
    # horizon 越长 → 有效样本越少 → 基准越高
    assert null_ic_benchmark(50, 720, 20) > null_ic_benchmark(50, 720, 5)
    # 退化输入安全
    assert null_ic_benchmark(0, 720, 5) == 0.0
    assert null_ic_benchmark(50, 0, 5) == 0.0


def test_null_ic_benchmark_sanity_value() -> None:
    """手算 sanity：N=50, samples=720, horizon=5 → n_eff=144, σ=1/√143≈0.0836。

    E[max|null] = σ·[(1−γ)Φ⁻¹(1−1/50) + γΦ⁻¹(1−1/(50e))] ≈ σ·2.215 ≈ 0.185。
    纯噪声里 50 个候选能"跑出" 0.18 级别的 |IC|——这正是要透出的事实。
    """
    from inalpha_factor.effectiveness import null_ic_benchmark

    v = null_ic_benchmark(50, 720, 5)
    assert 0.15 < v < 0.22

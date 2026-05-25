"""回测稳健性（robustness）指标 —— PBO / Bootstrap Sharpe CI / Deflated Sharpe。

**为什么单加这一层**：``engine.metrics`` 只算单策略单回测的指标
（sharpe / sortino / max_drawdown）。当你跑 **多组合对比**（``swarm.run_backtest_grid``
笛卡尔积，N 个策略 × M 个标的）后，"挑出最优组合"这个动作本身会引入
**多重检验偏差**——白噪声跑 100 次也能挑出一个 Sharpe=2.0 的"赢家"。

本模块给三个学术界标准的过拟合检测指标：

- ``probability_of_backtest_overfitting``（PBO，CSCV 算法 [Bailey & López de Prado 2014]）
- ``bootstrap_sharpe_ci``（重采样置信区间）
- ``deflated_sharpe_ratio``（DSR，对"试了 N 个策略"做修正）

**何时用**：

- 跑完 ``swarm.run_backtest_grid`` 拿到 N 个策略的 returns 矩阵 → PBO
- 单策略想知道 Sharpe 置信区间是不是跨 0 → ``bootstrap_sharpe_ci``
- 单策略 Sharpe 看起来好，想验证不是从 N 次试错中挑出来的 → ``deflated_sharpe_ratio``

**何时不用**：

- 单次单策略回测，不打算和别的组合对比 → ``engine.metrics`` 够了
- 实盘信号判断 → 这是事后统计，不适合实时决策路径

**实现注**：

- Wrap 自 `bcosm/backtester-mcp <https://github.com/bcosm/backtester-mcp>`_
  （Apache 2.0）的 ``robustness`` 模块。我们只暴露三个**纯数学函数**——
  ``perturbation_pbo`` / ``walk_forward`` 那两个绑死他们的 backtest engine，
  跟 Inalpha BacktestEngine 不兼容，留待后续 ADR 决定怎么接（可能需要
  ``services/evolver`` D-10+ 才用得上）。
- 输入接受 ``Sequence[float]`` 或 numpy，输出统一 dataclass，便于 FastAPI 序列化。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from backtester_mcp.robustness import (
    bootstrap_sharpe as _bt_bootstrap_sharpe,
)
from backtester_mcp.robustness import (
    deflated_sharpe as _bt_deflated_sharpe,
)
from backtester_mcp.robustness import (
    pbo as _bt_pbo,
)


@dataclass(frozen=True, slots=True)
class PBOResult:
    """PBO（Probability of Backtest Overfitting）报告。

    Attributes:
        pbo: ``[0, 1]``，越接近 1 越可能过拟合；> 0.5 警戒。
        n_combinations: CSCV 实际跑了几个 in-sample/out-of-sample 分组组合。
        logit_mean: rank logit 均值；< 0 提示选最优策略在 OOS 倾向 underperform。
    """

    pbo: float
    n_combinations: int
    logit_mean: float


@dataclass(frozen=True, slots=True)
class BootstrapSharpeResult:
    """Bootstrap Sharpe 置信区间。

    Attributes:
        sharpe: 点估计 Sharpe（无年化）。
        ci_lower: 置信区间下界。
        ci_upper: 置信区间上界。
        ci_includes_zero: 区间是否横跨 0；True 表示"统计上不显著为正"。
    """

    sharpe: float
    ci_lower: float
    ci_upper: float
    ci_includes_zero: bool


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    """Deflated Sharpe Ratio（DSR）。

    Attributes:
        dsr: deflated test statistic；越大越显著。
        p_value: 单边 p 值（"白噪声里挑最佳能达到这个 Sharpe"的概率）。
        expected_max_sharpe: 在 ``n_strategies`` 个独立白噪声策略下期望最大 Sharpe。
    """

    dsr: float
    p_value: float
    expected_max_sharpe: float


def _as_returns_matrix(returns_by_strategy: Sequence[Sequence[float]]) -> np.ndarray:
    """把 list-of-returns 转成 ``(n_periods, n_strategies)`` numpy 矩阵。

    要求每条 returns 等长；不等长抛 ``ValueError``（不静默截断）。
    """
    if len(returns_by_strategy) < 2:
        raise ValueError("PBO 至少需要 2 个策略的 returns")
    lengths = {len(r) for r in returns_by_strategy}
    if len(lengths) != 1:
        raise ValueError(
            f"所有 returns 序列必须等长；实际长度集合 = {sorted(lengths)}"
        )
    return np.column_stack([np.asarray(r, dtype=np.float64) for r in returns_by_strategy])


def probability_of_backtest_overfitting(
    returns_by_strategy: Sequence[Sequence[float]],
    n_splits: int = 16,
) -> PBOResult:
    """跑 CSCV-PBO（Combinatorially Symmetric Cross-Validation）。

    Args:
        returns_by_strategy: 多策略的 bar returns；外层每条对应一个策略 / 组合，
            内层是该策略每根 bar 的收益率（必须等长）。
        n_splits: CSCV 切分块数（默认 16）；序列短可调小，但 ``n_periods / n_splits``
            必须 ≥ 2，否则抛错。

    Returns:
        ``PBOResult``，``pbo > 0.5`` 提示组合搜索严重过拟合。

    Raises:
        ValueError: 策略数 < 2、长度不一致、或 ``n_splits`` 太大。
    """
    matrix = _as_returns_matrix(returns_by_strategy)
    raw = _bt_pbo(matrix, n_splits=n_splits)
    return PBOResult(
        pbo=float(raw["pbo"]),
        n_combinations=int(raw["n_combinations"]),
        logit_mean=float(raw.get("logit_mean", 0.0)),
    )


def bootstrap_sharpe_ci(
    returns: Sequence[float],
    n_samples: int = 10_000,
    ci: float = 0.95,
    seed: int = 42,
) -> BootstrapSharpeResult:
    """对单序列 returns 做 bootstrap，得 Sharpe 置信区间。

    Args:
        returns: bar returns 序列。
        n_samples: 重采样次数；默认 10k；序列短可调 1000 加速。
        ci: 置信水平，默认 0.95。
        seed: 随机种子（保证测试可复现）。

    Returns:
        ``BootstrapSharpeResult``；``ci_includes_zero=True`` 提示 Sharpe 不显著为正。
    """
    arr = np.asarray(returns, dtype=np.float64)
    if arr.size < 2:
        raise ValueError("returns 序列长度必须 ≥ 2")
    raw = _bt_bootstrap_sharpe(arr, n_samples=n_samples, ci=ci, seed=seed)
    return BootstrapSharpeResult(
        sharpe=float(raw["sharpe"]),
        ci_lower=float(raw["ci_lower"]),
        ci_upper=float(raw["ci_upper"]),
        ci_includes_zero=bool(raw["ci_includes_zero"]),
    )


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_returns: int,
    n_strategies: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> DeflatedSharpeResult:
    """López de Prado 2014 的 Deflated Sharpe。

    场景：你跑了 ``n_strategies`` 个策略，挑出 Sharpe 最高的一个；这个函数告诉你
    "白噪声跑同样次数能不能也凑出这个 Sharpe"。

    Args:
        observed_sharpe: 选出的最佳策略的 Sharpe（年化或非年化都行，要跟 n_returns
            的频率对齐）。
        n_returns: 用于计算 Sharpe 的 returns 长度。
        n_strategies: 总共试了几个策略（包括筛掉的）。
        skew / kurtosis: returns 分布的高阶矩；默认 0/3（正态假设）。

    Returns:
        ``DeflatedSharpeResult``；``p_value < 0.05`` 提示这个 Sharpe 在多重检验下仍显著。
    """
    if n_strategies < 1:
        raise ValueError("n_strategies 必须 ≥ 1")
    if n_returns < 2:
        raise ValueError("n_returns 必须 ≥ 2")
    raw = _bt_deflated_sharpe(
        observed_sharpe=float(observed_sharpe),
        n_returns=int(n_returns),
        n_strategies=int(n_strategies),
        skew=float(skew),
        kurtosis=float(kurtosis),
    )
    return DeflatedSharpeResult(
        dsr=float(raw["dsr"]),
        p_value=float(raw["p_value"]),
        expected_max_sharpe=float(raw.get("expected_max_sharpe", 0.0)),
    )


__all__ = [
    "BootstrapSharpeResult",
    "DeflatedSharpeResult",
    "PBOResult",
    "bootstrap_sharpe_ci",
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
]

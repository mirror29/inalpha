"""**Baseline 与适配器策略** + 策略 ID 注册表（D-9 起重新定位）。

> **架构演变 · 2026-05-25**：
> D-7 时这里是"内置策略库"，D-8c 时给 ``compose_strategy`` 当路由出口。
> **D-9 之后**：随着 ADR-0020 E1 MVP（``strategy_authoring``）落地，agent 可以
> 自己写完整 ``Strategy`` 子类源码。此目录里的策略**降级为基线对照 / 教学 / 适配**，
> 不再是"穷举库的一部分"。穷举策略空间是 ``strategy_candidates`` 表的职责——
> LLM 写多少进多少，promote 后人工管理。

**当前角色分类**：

| ID | 角色 | 何时被调用 |
|---|---|---|
| ``buy_and_hold`` | **首要基线** | 任何 candidate 回测都自动并跑一次（runner candidate 分支强制行为），fitness 对照 |
| ``sma_cross`` | **教学样本 + 协议契约参考** | ``paper.author_strategy`` description 内嵌简化版作 few-shot；用户明确点名时走 compose 快速通道 |
| ``mean_reversion`` | 同 sma_cross（教学样本） | 同上 |
| ``signal_replay`` | **adapter**（D-9 sandbox spike） | LLM 在沙盒生成纯函数 ``generate_signals(bars) -> list[signal]``，本类把 signals 重放进 BacktestEngine |

alpha 的定义 = candidate.fitness 显著高于 baseline.fitness（buy_and_hold）。
本注册表**不**应再积累更多策略——具体行情下的策略请走 author / signal_replay 路径。
"""
from ..strategy.base import Strategy
from .buy_and_hold import BuyAndHoldStrategy
from .mean_reversion import MeanReversionStrategy
from .signal_replay import SignalReplayStrategy
from .sma_cross import SMACrossStrategy

__all__ = [
    "BASELINE_BUY_AND_HOLD",
    "BuyAndHoldStrategy",
    "MeanReversionStrategy",
    "SMACrossStrategy",
    "SignalReplayStrategy",
    "get_strategy_class",
    "list_strategies",
]


# 名字常量 —— 避免 runner 等调用方硬编码字符串
BASELINE_BUY_AND_HOLD: str = "buy_and_hold"


_BASELINES: dict[str, type[Strategy]] = {
    BASELINE_BUY_AND_HOLD: BuyAndHoldStrategy,
    "sma_cross": SMACrossStrategy,
    "mean_reversion": MeanReversionStrategy,
    # D-9 sandbox spike：把 LLM 在沙盒生成的 signals 重放进 BacktestEngine（轻量 candidate 路径）
    "signal_replay": SignalReplayStrategy,
}


def get_strategy_class(strategy_id: str) -> type[Strategy]:
    """返回 strategy_id 对应的 baseline / adapter 类。未注册抛 ``KeyError``。"""
    if strategy_id not in _BASELINES:
        raise KeyError(
            f"unknown strategy_id {strategy_id!r}; available baselines: {sorted(_BASELINES.keys())}"
        )
    return _BASELINES[strategy_id]


def list_strategies() -> list[str]:
    """已注册的 baseline / adapter strategy_id。

    **不**是穷举库——agent 自创策略在 ``strategy_candidates`` 表，由 LLM 写多少进多少。
    """
    return sorted(_BASELINES.keys())

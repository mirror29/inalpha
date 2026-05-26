"""Strategy compose engine —— D-8c · research→strategy 机器路径。

把 ``StrategyHint``（research 服务输出）路由到已注册的 strategy_id +
正规化后的 params。**MVP 阶段规则显式硬编码**，理由：

- 简单可调试 —— 出问题立刻能定位是 hint 错还是规则错
- LLM 已经在 research 端推过一次 family；compose 不应该让 LLM 再选第二次（防"双 LLM 互相同意"，[ADR-0012 Alt D]）

D-9 起：**compose 仍是首选**（成本低、稳定）；compose 拒绝（``family='none'`` 或硬约束失败）
时，orchestrator 走 **``paper.author_strategy`` 路径**让 LLM 直接写完整 ``Strategy`` 子类
源码（ADR-0020 E1 MVP，见 ``services/paper/src/inalpha_paper/strategy_authoring/``）。
本模块的规则路由对内置 3 策略仍然有效——只是不再是唯一出口。

服务边界注意：本模块的 ``StrategyHint`` / ``Factor`` 是 **paper 服务内部 owned**，
与 ``services/research`` 的同名 schema **字段一致但物理独立**。orchestration 层做
JSON 透传 —— 这样 paper 可以独立部署 / 独立演化，不依赖 research 包。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ────────────────────────────────────────────────────────────────────
# 输入契约（与 services/research 镜像，paper 内部 owned）
# ────────────────────────────────────────────────────────────────────


FactorKind = Literal["momentum", "mean_reversion", "volatility", "macro", "sentiment"]

StrategyFamily = Literal["trend", "mean_reversion", "buy_hold", "none"]

Horizon = Literal["intraday", "swing", "position"]


class Factor(BaseModel):
    """输入因子（research 服务产物的镜像 schema）。"""

    name: str
    kind: FactorKind
    value: float | str
    strength: float = Field(..., ge=0.0, le=1.0)
    horizon: Horizon = "swing"
    explanation: str = ""


class StrategyHint(BaseModel):
    """输入策略提示（research 服务产物的镜像 schema）。"""

    family: StrategyFamily = "none"
    params: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


class ComposeRequest(BaseModel):
    """``POST /strategies/compose`` 请求体。"""

    hint: StrategyHint
    factors: list[Factor] = Field(default_factory=list)
    timeframe: str = Field(default="1h", description="目标 K 线周期，影响参数范围")


class ComposeResult(BaseModel):
    """compose 输出。``strategy_id == None`` 表示拒绝。"""

    strategy_id: str | None = Field(
        default=None,
        description="路由到的 strategy_id；family='none' 或硬约束失败时为 None",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="正规化后的策略参数（保证可被对应 Strategy 构造器消费）",
    )
    reasoning: str = Field(
        default="",
        description="人话解释为什么选这个 strategy + 这套参数（含 hint.reasoning）",
    )
    rejected_reason: str | None = Field(
        default=None,
        description="为 None 表示成功；否则说明拒绝原因",
    )


# ────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────


def compose_strategy(
    hint: StrategyHint,
    factors: list[Factor],
    *,
    timeframe: str = "1h",
) -> ComposeResult:
    """路由 + 正规化参数。返回 ``ComposeResult``。

    路由表：
    - ``trend``           → ``sma_cross``       (fast_period / slow_period / trade_size)
    - ``mean_reversion``  → ``mean_reversion``  (period / std_mult / trade_size)
    - ``buy_hold``        → ``buy_and_hold``    (trade_size)
    - ``none``            → reject

    参数正规化原则：
    - LLM 给的 params 字段名宽松（``num_std`` / ``std_mult`` 都接受）→ 统一映射
    - 数值越界自动 clip 到该 family 的合法区间
    - 缺字段用 horizon × factor.strength 推默认
    """
    if hint.family == "none":
        return ComposeResult(
            strategy_id=None,
            rejected_reason="strategy_hint.family == 'none' (research 没给可执行建议)",
        )

    if hint.family == "trend":
        return _compose_trend(hint, factors, timeframe=timeframe)
    if hint.family == "mean_reversion":
        return _compose_mean_reversion(hint, factors, timeframe=timeframe)
    if hint.family == "buy_hold":
        return _compose_buy_hold(hint, factors)

    return ComposeResult(
        strategy_id=None,
        rejected_reason=f"unknown family {hint.family!r}",
    )


# ────────────────────────────────────────────────────────────────────
# family-specific 组装器
# ────────────────────────────────────────────────────────────────────


def _compose_trend(
    hint: StrategyHint,
    factors: list[Factor],
    *,
    timeframe: str,
) -> ComposeResult:
    """trend → sma_cross。

    - fast_period: 5-20，hint > momentum 因子主导 horizon > 默认
    - slow_period: fast_period × 3 起步，clip 到 20-60
    - trade_size: signal/factor 强度加权 × max_position_pct，clip 到 0.01-0.05
    """
    fast = _coerce_int(hint.params.get("fast_period"), 5, 20, default=10)
    slow_raw = hint.params.get("slow_period")
    slow = _coerce_int(slow_raw, 20, 60, default=max(fast * 3, 20))
    if slow <= fast:
        # 必须 slow > fast，强制拉开
        slow = min(60, fast * 3)
        if slow <= fast:
            slow = fast + 1

    trade_size = _coerce_float(
        hint.params.get("trade_size"),
        0.01,
        0.05,
        default=_recommend_trade_size(factors, base=0.02),
    )
    # position_pct 让 sma_cross 真正按本金比例下单（runner 注入 initial_cash 后
    # 生效）；缺省 1.0 = 信号触发时满仓。LLM hint 可显式覆盖 0-1 间。
    position_pct = _coerce_float(
        hint.params.get("position_pct"),
        0.0,
        1.0,
        default=1.0,
    )

    return ComposeResult(
        strategy_id="sma_cross",
        params={
            "fast_period": fast,
            "slow_period": slow,
            "trade_size": trade_size,
            "position_pct": position_pct,
        },
        reasoning=_compose_reasoning(
            "sma_cross", hint, factors,
            extras=f"fast={fast} slow={slow} size={trade_size:.3f} pos_pct={position_pct:.2f}",
        ),
    )


def _compose_mean_reversion(
    hint: StrategyHint,
    factors: list[Factor],
    *,
    timeframe: str,
) -> ComposeResult:
    """mean_reversion → mean_reversion。

    - period: 10-30
    - std_mult: 1.5-2.5（LLM 可能用 ``num_std`` 字段名，统一映射）
    - trade_size: 0.01-0.05
    """
    period = _coerce_int(hint.params.get("period"), 10, 30, default=20)

    # 接受 std_mult / num_std 两种字段名
    raw_std = hint.params.get("std_mult", hint.params.get("num_std"))
    std_mult = _coerce_float(raw_std, 1.5, 2.5, default=2.0)

    trade_size = _coerce_float(
        hint.params.get("trade_size"),
        0.01,
        0.05,
        default=_recommend_trade_size(factors, base=0.02),
    )

    return ComposeResult(
        strategy_id="mean_reversion",
        params={
            "period": period,
            "std_mult": std_mult,
            "trade_size": trade_size,
        },
        reasoning=_compose_reasoning(
            "mean_reversion", hint, factors,
            extras=f"period={period} std_mult={std_mult:.2f} size={trade_size:.3f}",
        ),
    )


def _compose_buy_hold(
    hint: StrategyHint,
    factors: list[Factor],
) -> ComposeResult:
    """buy_hold → buy_and_hold。``trade_size`` 是绝对订单数量（BTC/ETH 单位），
    保守默认 0.01；D-9 candidate 路径下 baseline qty 由 ``runner.py`` 按
    ``initial_cash / first_open`` 预算后注入，compose 这条路径仅供 LLM hint
    直选 buy_hold family 时落地用。"""
    trade_size = _coerce_float(
        hint.params.get("trade_size"),
        0.001,
        1.0,
        default=0.01,
    )
    return ComposeResult(
        strategy_id="buy_and_hold",
        params={"trade_size": trade_size},
        reasoning=_compose_reasoning(
            "buy_and_hold", hint, factors,
            extras=f"size={trade_size:.3f}",
        ),
    )


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────


def _coerce_int(value: Any, lo: int, hi: int, *, default: int) -> int:
    """把 LLM 给的任意值转 int 并 clip 到 [lo, hi]。失败用 default。"""
    try:
        x = round(float(value)) if value is not None else default
    except (TypeError, ValueError):
        x = default
    return max(lo, min(hi, x))


def _coerce_float(value: Any, lo: float, hi: float, *, default: float) -> float:
    try:
        x = float(value) if value is not None else default
    except (TypeError, ValueError):
        x = default
    return max(lo, min(hi, x))


def _recommend_trade_size(factors: list[Factor], *, base: float) -> float:
    """按因子最大 strength 缩放交易规模。strength 1.0 时 = base × 1.5；0 时 = base × 0.5。"""
    if not factors:
        return base
    max_strength = max(f.strength for f in factors)
    return base * (0.5 + max_strength)


def _compose_reasoning(
    strategy_id: str,
    hint: StrategyHint,
    factors: list[Factor],
    *,
    extras: str,
) -> str:
    base = f"→ {strategy_id} | {extras}"
    if hint.reasoning:
        base = f"{hint.reasoning} ; {base}"
    if factors:
        names = ", ".join(f.name for f in factors[:3])
        base += f" | factors: {names}"
    return base

"""协议契约校验 —— ``dynamic_loader`` 之后跑（沙盒第 2 道关·verify 阶段）。

确保 LLM 写出来的策略类**真的能被 BacktestEngine 实例化**：

- 必须直接或间接继承 ``Strategy``（``dynamic_loader`` 已查过，这里 belt-and-suspenders）
- 必须覆写 ``on_bar``（不能裸继承父类 stub —— 那样跑回测毫无信号）
- ``__init__`` 签名必须接受 ``(name, clock, msgbus, instrument_id, timeframe, ...)``
  —— 这是 ``runner.run_engine_in_subprocess`` 的实例化约定

校验失败抛 ``ContractError``，调用方应把 message 回给 LLM 让它修源码。
"""
from __future__ import annotations

import inspect

from ..strategy.base import Strategy


class ContractError(ValueError):
    """协议契约不满足。message 是给 LLM 看的、可操作的修改提示。"""


# Engine 实例化策略时必传的关键字参数（``runner.run_engine_in_subprocess``）
_REQUIRED_INIT_KW: tuple[str, ...] = (
    "name",
    "clock",
    "msgbus",
    "instrument_id",
    "timeframe",
)


def verify_strategy_contract(cls: type) -> None:
    """对加载出来的策略类做协议契约校验。

    Args:
        cls: ``dynamic_loader.load_strategy_class`` 返回的策略子类

    Raises:
        ContractError: 任何契约不满足
    """
    # 1. 继承关系（dynamic_loader 已查过；这里二次确认是 defense in depth）
    if not (isinstance(cls, type) and issubclass(cls, Strategy)):
        raise ContractError(
            f"{cls.__name__ if isinstance(cls, type) else cls!r} 不是 Strategy 子类"
        )

    # 2. 必须覆写 on_bar —— 而不是裸继承父类（Strategy.on_bar 是 Actor 的 stub）
    own_on_bar = cls.__dict__.get("on_bar")
    if own_on_bar is None:
        # 检查 MRO 上有没有除 Strategy / Actor 之外的中间类覆写——MVP 不支持多层继承
        raise ContractError(
            f"{cls.__name__} 必须在类体里覆写 on_bar(self, bar)。"
            "没有 on_bar 的策略不会响应行情，回测无意义"
        )
    if not callable(own_on_bar):
        raise ContractError(f"{cls.__name__}.on_bar 必须是方法，不能是属性")

    # 3. __init__ 签名 —— 关键字参数必须能接收 engine 注入的 5 个字段
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError) as exc:
        raise ContractError(
            f"无法 inspect {cls.__name__}.__init__ 签名：{exc}"
        ) from exc

    params = sig.parameters
    # 第一个参数应该是 self（绑定方法 inspect 时仍然带 self，因为我们 inspect 的是类）
    param_names = [p for p in params if p != "self"]

    # 检查 5 个必传 kw 是否都能接收
    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    missing: list[str] = []
    for kw in _REQUIRED_INIT_KW:
        if kw in param_names:
            continue
        if accepts_var_keyword:
            # **kwargs 兜底——MVP 接受这个形式（虽然不推荐）
            continue
        missing.append(kw)

    if missing:
        raise ContractError(
            f"{cls.__name__}.__init__ 缺少必要参数 {missing}。"
            f"签名必须是 `def __init__(self, name, clock, msgbus, instrument_id, "
            "timeframe='1h', ...你的策略参数): ...`"
        )

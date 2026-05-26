"""把 LLM 写的策略源码字符串加载成 ``Strategy`` 子类（沙盒第 2 道关·load 阶段）。

**前置**：调用前必须先过 ``ast_audit.audit_strategy_code(code).ok``。本模块假定
源码已审计；如果跳过审计直接调本函数 = 绕过沙盒，禁止。

设计：

- ``compile()`` + ``exec()`` 在**受限 globals** 里运行
- 受限 globals 注入 inalpha 内核必要符号，LLM 不需要 import 任何 inalpha 模块
- 受限 globals 的 ``__builtins__`` 是裁剪过的子集（``ast_audit`` 已拦但 defense in depth）
- exec 后从 namespace 里捞出唯一一个 ``Strategy`` 子类
"""
from __future__ import annotations

import builtins
from collections import deque
from typing import Any, Final
from uuid import uuid4

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import Strategy


class DynamicLoadError(RuntimeError):
    """加载失败（exec 报错、找不到子类、找到多个子类等）。"""


# 裁剪后的 __builtins__——只留纯计算需要的、无副作用的内置
# （ast_audit 已经拦了 eval/exec/open/__import__ 等，这里二次保险）
_SAFE_BUILTINS: Final[dict[str, Any]] = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "bytes", "callable", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hash", "hex", "id", "int", "isinstance", "issubclass", "iter", "len",
        "list", "map", "max", "min", "next", "object", "oct", "ord", "pow",
        "print", "range", "repr", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "type", "zip",
        # 异常类——LLM 写策略时可能 raise ValueError 校验参数
        "Exception", "ValueError", "TypeError", "KeyError", "RuntimeError",
        "IndexError", "ZeroDivisionError", "ArithmeticError", "AssertionError",
        # 用于 super() 和类相关
        "super", "property", "staticmethod", "classmethod",
        # True/False/None 是关键字不在 builtins，但 NotImplemented 是
        "NotImplemented",
    )
}
# Python 的 ``class X: ...`` 语法编译后会调 ``__build_class__``——必须暴露，
# 否则 exec 时报 ``NameError: __build_class__ not found``。这个内置本身不能被
# LLM 通过名字访问（ast_audit 不在 _DENIED_NAMES 也不该让 LLM 直接调它，因为
# 它的签名是隐式的——LLM 写不出能成功调用的 invocation），所以放进来安全。
_SAFE_BUILTINS["__build_class__"] = builtins.__build_class__


def _build_restricted_globals() -> dict[str, Any]:
    """构造受限 globals —— inalpha 内核符号 + 裁剪 __builtins__。

    每次调用返回新 dict，避免跨候选源码污染。
    """
    return {
        "__builtins__": _SAFE_BUILTINS,
        "__name__": "<strategy_candidate>",
        "__doc__": None,
        # 内核基类 / 工具
        "Strategy": Strategy,
        "Clock": Clock,
        "MessageBus": MessageBus,
        "ClientOrderId": ClientOrderId,
        "InstrumentId": InstrumentId,
        "StrategyId": StrategyId,
        # 数据 / 订单 model
        "Bar": Bar,
        "Order": Order,
        "OrderSide": OrderSide,
        "OrderType": OrderType,
        # 事件 model（策略回调入参）
        "OrderSubmitted": OrderSubmitted,
        "OrderAccepted": OrderAccepted,
        "OrderFilled": OrderFilled,
        "OrderRejected": OrderRejected,
        "OrderCanceled": OrderCanceled,
        "PositionOpened": PositionOpened,
        "PositionChanged": PositionChanged,
        "PositionClosed": PositionClosed,
        # 常用 stdlib 符号（免 import）
        "deque": deque,
        "uuid4": uuid4,
    }


def load_strategy_class(code: str) -> type[Strategy]:
    """compile + exec 策略源码，捞出唯一 ``Strategy`` 子类。

    Args:
        code: 已过 ``ast_audit`` 的源码字符串

    Returns:
        ``Strategy`` 子类（**类对象**，不是实例）

    Raises:
        DynamicLoadError: compile / exec 失败、找到 0 个 / 多个 ``Strategy`` 子类

    **不**抓 LLM 自己代码里的 ValueError 等——那是策略逻辑 bug，应该让上层感知。
    本函数只关心"加载阶段"的异常。
    """
    try:
        compiled = compile(code, filename="<strategy_candidate>", mode="exec")
    except SyntaxError as exc:
        # ast_audit 已经会拦 SyntaxError，但 compile 还可能对一些 ast.parse 通过的
        # 源码报二级错（如 return 在 module-level）—— defense in depth
        raise DynamicLoadError(f"compile 失败：{exc.msg} (line {exc.lineno})") from exc

    namespace: dict[str, Any] = {}
    restricted_globals = _build_restricted_globals()
    try:
        exec(compiled, restricted_globals, namespace)
    except Exception as exc:
        raise DynamicLoadError(
            f"exec 策略源码失败：{type(exc).__name__}: {exc}"
        ) from exc

    # 找 Strategy 子类（必须是 namespace 里**新定义**的，不能是注入的 Strategy 本身）
    candidates: list[type[Strategy]] = []
    for value in namespace.values():
        if (
            isinstance(value, type)
            and issubclass(value, Strategy)
            and value is not Strategy
        ):
            candidates.append(value)

    if not candidates:
        raise DynamicLoadError(
            "源码里没有继承 Strategy 的子类。必须 `class XxxStrategy(Strategy): ...`"
        )
    if len(candidates) > 1:
        names = ", ".join(c.__name__ for c in candidates)
        raise DynamicLoadError(
            f"源码里有多个 Strategy 子类：{names}。MVP 只允许 1 个，请删除多余的"
        )

    return candidates[0]

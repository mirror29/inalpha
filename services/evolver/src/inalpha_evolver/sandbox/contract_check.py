"""契约校验 —— 薄封装 paper 的 ``load_strategy_class`` + ``verify_strategy_contract``。"""
from __future__ import annotations

from inalpha_paper.strategy_authoring.contract_check import verify_strategy_contract
from inalpha_paper.strategy_authoring.dynamic_loader import load_strategy_class

from ..exceptions import SandboxError


def assert_strategy_subclass(code: str) -> None:
    """确保源码能加载为合法的 ``Strategy`` 子类并满足协议契约。

    步骤：
    1. ``load_strategy_class(code)`` —— compile + exec + 捞子类
    2. ``verify_strategy_contract(cls)`` —— 检查覆写 ``on_bar`` 等强制回调

    Args:
        code: 已通过 AST 审计的源码字符串。

    Raises:
        SandboxError: 加载失败 / 契约不满足。
    """
    try:
        cls = load_strategy_class(code)
    except Exception as exc:
        raise SandboxError(f"策略类加载失败：{exc}") from exc

    try:
        verify_strategy_contract(cls)
    except Exception as exc:
        raise SandboxError(f"策略契约校验失败：{exc}") from exc
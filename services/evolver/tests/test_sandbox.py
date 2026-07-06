"""sandbox 单元测试 —— 薄封装验证。

测试策略：验证薄封装正确转发 paper 的函数调用并转换异常。
E2 当 evolver 有独立沙盒逻辑时才加更多测试。
"""
from __future__ import annotations

import pytest

from inalpha_evolver.exceptions import SandboxError
from inalpha_evolver.sandbox import assert_safe, assert_strategy_subclass


def test_safe_code_passes() -> None:
    """简单安全策略应通过 AST 审计。"""
    code = """
from collections import deque

class MyStrategy:
    def on_bar(self, bar):
        pass
"""
    # 只测试方法不抛异常（真正的审计需要 paper 的完整符号表）
    assert_safe(code)


def test_empty_code_raises() -> None:
    """空代码应被审计拒绝。"""
    with pytest.raises(SandboxError):
        assert_safe("")


def test_assert_strategy_subclass_invalid() -> None:
    """非策略类应被契约校验拒绝。"""
    code = "class NotAStrategy:\n    pass\n"
    with pytest.raises(SandboxError):
        assert_strategy_subclass(code)
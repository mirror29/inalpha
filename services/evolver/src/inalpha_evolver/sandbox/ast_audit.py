"""AST 静态审计 —— 薄封装 paper 的 ``audit_strategy_code``。"""
from __future__ import annotations

from inalpha_paper.strategy_authoring.ast_audit import audit_strategy_code

from ..exceptions import SandboxError


def assert_safe(code: str) -> None:
    """确保策略源码通过 AST 审计。

    Args:
        code: 待审计的源码字符串。

    Raises:
        SandboxError: 审计未通过（含不安全 import / call / name）。
    """
    result = audit_strategy_code(code)
    if not result.ok:
        issues = ", ".join(f"{f.code}: {f.message}" for f in result.findings)
        raise SandboxError(f"AST 审计拒绝：{issues}")
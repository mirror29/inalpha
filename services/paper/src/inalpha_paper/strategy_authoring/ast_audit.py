"""AST 静态审计 —— 拒掉 LLM 写的危险策略源码（沙盒第 1 道关）。

**为什么必须在 main 进程跑**：子进程隔离只防"运行时危害"，但代码进子进程前
``import`` 已经触发任意 module 初始化代码。AST 必须在 import / exec 之前拒掉
危险源码。

设计原则：**白名单优于黑名单**。LLM 写策略不需要碰 OS / 网络 / 反射 / 动态执行，
只允许纯计算 + 已注入的 inalpha 符号。任何超出白名单的访问 → 拒绝。

允许的 stdlib import（无副作用、纯计算）：
- ``math`` / ``statistics`` —— 数值
- ``collections`` —— deque 等容器
- ``dataclasses`` / ``typing`` / ``enum`` —— 类型工具
- ``json`` —— 反序列化 LLM 拼的常量（极少用）

明确拒绝（即使被白名单匹配也拦）：
- ``eval`` / ``exec`` / ``compile`` / ``__import__`` —— 动态执行
- ``open`` 带写模式 / ``input`` —— I/O
- ``getattr`` / ``setattr`` / ``delattr`` / ``globals`` / ``locals`` / ``vars`` —— 反射
- ``breakpoint`` —— 调试中断
- 双下划线属性访问（``obj.__class__`` / ``obj.__bases__`` 等）—— 跳出沙盒经典路径
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Final

# 允许 import 的 stdlib 模块（与 dynamic_loader.RESTRICTED_GLOBALS 内置符号互补——
# 后者已经把 Strategy / Bar / Order / OrderSide / OrderType / ClientOrderId /
# InstrumentId / Clock / MessageBus / OrderFilled / PositionOpened /
# PositionClosed / deque / uuid4 注入了，LLM 不需要再 import 这些）
_ALLOWED_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "math",
        "statistics",
        "collections",
        "dataclasses",
        "typing",
        "enum",
        "json",
    }
)

# 禁掉的内置名（即使 builtins 里有也拦在 AST 层）
_DENIED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "memoryview",
        "exit",
        "quit",
        "help",
        "credits",
        "license",
        "copyright",
    }
)

# 禁掉的 dunder 属性访问（双下划线开头）—— 走这些是经典越狱路径
# 例：``().__class__.__bases__[0].__subclasses__()`` → 拿到 OSError 等任意类
_ALLOWED_DUNDERS: Final[frozenset[str]] = frozenset(
    {
        "__init__",
        "__name__",
        "__doc__",
        "__qualname__",
        "__module__",
    }
)


@dataclass(slots=True, frozen=True)
class AuditFinding:
    """单条违规。``lineno`` / ``col_offset`` 指向源码位置，便于 LLM 收到反馈后定位。"""

    code: str
    """机器可读的违规类型（IMPORT_DENIED / NAME_DENIED / DUNDER_ACCESS / ...）"""

    message: str
    """人话解释"""

    lineno: int
    col_offset: int


@dataclass(slots=True)
class AuditResult:
    """审计结果。``ok`` = ``True`` 表示通过；``findings`` 是命中的违规列表。"""

    ok: bool
    findings: list[AuditFinding] = field(default_factory=list)

    def reason(self) -> str:
        """把 findings 合成一段 LLM 可读的拒绝理由。"""
        if self.ok:
            return ""
        lines = [
            f"line {f.lineno}: [{f.code}] {f.message}"
            for f in self.findings
        ]
        return "策略代码被沙盒拒绝（共 {} 处违规）：\n{}".format(
            len(self.findings), "\n".join(lines)
        )


def audit_strategy_code(code: str) -> AuditResult:
    """对一段策略源码做 AST 静态审计。

    Args:
        code: LLM 写的完整 Python 源码（应只含 1 个 ``Strategy`` 子类）

    Returns:
        ``AuditResult``：``ok=True`` 时可放行进 ``dynamic_loader.load_strategy_class``；
        否则 ``findings`` 列出所有违规，调用方应把 ``reason()`` 回给 LLM 让它重写。

    本函数**只**做静态分析，不真正 exec 任何代码——安全。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return AuditResult(
            ok=False,
            findings=[
                AuditFinding(
                    code="SYNTAX_ERROR",
                    message=f"无法解析 Python 源码：{exc.msg}",
                    lineno=exc.lineno or 0,
                    col_offset=exc.offset or 0,
                )
            ],
        )

    visitor = _AuditVisitor()
    visitor.visit(tree)
    return AuditResult(ok=not visitor.findings, findings=visitor.findings)


class _AuditVisitor(ast.NodeVisitor):
    """走一遍 AST 收集所有违规。不抛异常，把违规累计到 ``findings``。"""

    def __init__(self) -> None:
        self.findings: list[AuditFinding] = []

    # ─── import 白名单 ───

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in _ALLOWED_IMPORTS:
                self._add(
                    node,
                    "IMPORT_DENIED",
                    f"不允许 import {alias.name!r}；仅允许 {sorted(_ALLOWED_IMPORTS)}",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # 拒绝相对 import（LLM 没有 __package__ 上下文，写了也错）
        if node.level and node.level > 0:
            self._add(
                node,
                "IMPORT_DENIED",
                "不允许相对 import（你的代码不在包内，无 __package__ 上下文）",
            )
            self.generic_visit(node)
            return
        module = node.module or ""
        top = module.split(".")[0]
        if top not in _ALLOWED_IMPORTS:
            self._add(
                node,
                "IMPORT_DENIED",
                f"不允许 from {module!r} import ...；仅允许 {sorted(_ALLOWED_IMPORTS)}"
                f"。inalpha 符号（Strategy / Bar / Order / ...）已在 globals 注入，"
                "直接用，不需要 import",
            )
        self.generic_visit(node)

    # ─── 危险名字访问 ───

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DENIED_NAMES:
            self._add(
                node,
                "NAME_DENIED",
                f"不允许使用名字 {node.id!r}（动态执行 / 反射 / I/O 类符号）",
            )
        self.generic_visit(node)

    # ─── dunder 属性访问 ───

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if (
            attr.startswith("__")
            and attr.endswith("__")
            and attr not in _ALLOWED_DUNDERS
        ):
            self._add(
                node,
                "DUNDER_ACCESS",
                f"不允许访问 dunder 属性 {attr!r}（经典沙盒越狱路径）",
            )
        self.generic_visit(node)

    # ─── 全局 / 非局部声明 ───

    def visit_Global(self, node: ast.Global) -> None:
        self._add(
            node,
            "GLOBAL_DENIED",
            "不允许 global 声明",
        )
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._add(
            node,
            "NONLOCAL_DENIED",
            "不允许 nonlocal 声明",
        )
        self.generic_visit(node)

    # ─── 异步 / 等待（策略是同步回调） ───

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add(
            node,
            "ASYNC_DENIED",
            "策略是同步回调，不允许 async def",
        )
        # 不递归——里面也不该有

    def visit_Await(self, node: ast.Await) -> None:
        self._add(node, "AWAIT_DENIED", "不允许 await")
        self.generic_visit(node)

    # ─── helpers ───

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        self.findings.append(
            AuditFinding(
                code=code,
                message=message,
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
            )
        )

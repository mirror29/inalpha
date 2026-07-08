from __future__ import annotations


class DiffApplyError(RuntimeError):
    """对源码应用 unified diff 失败（行号偏移过大 / 无法匹配）。"""

    def __init__(
        self,
        message: str,
        original: str | None = None,
        failed_diff: str | None = None,
    ) -> None:
        self.original = original
        self.failed_diff = failed_diff
        super().__init__(message)


class SandboxError(RuntimeError):
    """沙盒拒绝（AST 审计失败 / 契约校验失败）。"""


class EvaluationError(RuntimeError):
    """回测评估失败。"""


class EvaluationTimeoutError(RuntimeError):
    """回测评估超时。"""


class StoreError(RuntimeError):
    """DB 存储操作失败。"""


class LLMError(RuntimeError):
    """LLM 调用失败。"""
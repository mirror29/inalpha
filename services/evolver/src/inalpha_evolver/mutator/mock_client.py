"""Mock 变异客户端 —— 返回手写 diff（测试用，不接真实 API）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from inalpha_shared_llm.client import MockLLMClient

from .diff_applier import apply_diff
from .llm_client import MutationResult


@dataclass(slots=True)
class MockMutator:
    """Mock mutator —— 直接返回预设的 unified diff 列表。

    用于单元测试 / E2E 测试，不接 LLM API。
    预设的 diff 应该：是有效的 unified diff、能被 ``apply_diff`` 应用、产生
    ``SMACrossStrategy`` 的有效变体。
    """

    diffs: list[str] = field(default_factory=list)
    max_fuzz: int = 3
    _call_count: int = 0

    async def mutate(
        self,
        current_source: str,
        report: dict[str, Any] | None = None,
        hint: str = "",
    ) -> MutationResult:
        """返回预设的 diff。

        Raises:
            RuntimeError: 调用次数超出预设 diff 数量。
        """
        if self._call_count >= len(self.diffs):
            msg = (
                f"MockMutator: 第 {self._call_count + 1} 次调用超出预设 diff 数量 "
                f"({len(self.diffs)})"
            )
            raise RuntimeError(msg)

        raw_diff = self.diffs[self._call_count].strip()
        self._call_count += 1

        if not raw_diff or not raw_diff.startswith("---"):
            return MutationResult(
                new_source=current_source,
                unified_diff=None,
                source_hash=sha256(current_source.encode()).hexdigest(),
                llm_cost_usd=0.0,
                cache_hit_tokens=0,
            )

        new_source = apply_diff(current_source, raw_diff, max_fuzz=self.max_fuzz)

        return MutationResult(
            new_source=new_source,
            unified_diff=raw_diff,
            source_hash=sha256(new_source.encode()).hexdigest(),
            llm_cost_usd=0.005,  # mock 固定费用
            cache_hit_tokens=0,
        )
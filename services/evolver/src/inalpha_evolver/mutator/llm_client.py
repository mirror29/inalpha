"""LLM 变异客户端 —— 包装 ``_shared/llm`` 的 LLMClient，组装 prompt 模板。"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from inalpha_shared_llm.client import LLMClient as SharedLLMClient
from inalpha_shared_llm.client import MockLLMClient as SharedMockLLMClient
from inalpha_shared_llm.types import MutationRequest

from ..exceptions import DiffApplyError, LLMError
from .diff_applier import apply_diff
from .prompt_templates import SYSTEM_PROMPT, build_user_prompt


@dataclass(slots=True)
class MutationResult:
    """单次变异的结果。"""

    new_source: str
    """变异后的策略源码。"""
    unified_diff: str | None
    """LLM 返回的原始 unified diff（存 DB 供 lineage 追溯）。"""
    source_hash: str
    """变异后源码的 SHA256 摘要（防重复）。"""
    llm_cost_usd: float
    """本次 LLM 调用的估算费用（美元）。"""
    cache_hit_tokens: int
    """本次 LLM 调用的缓存命中 tokens（用于 cache 效率统计）。"""


@dataclass(slots=True)
class Mutator:
    """变异算子 —— 装箱 LLM 调用 + diff 应用 + 校验。

    E1 使用真实 LLM（通过 ``_shared/llm`` 的 LLMClient）。
    测试时可换 ``MockLLMClient``。
    """

    llm_client: SharedLLMClient | SharedMockLLMClient = field(
        default_factory=SharedLLMClient
    )
    max_fuzz: int = 3

    async def mutate(
        self,
        current_source: str,
        report: dict | None = None,
        hint: str = "",
    ) -> MutationResult:
        """执行一次 LLM 变异。

        Args:
            current_source: 当前策略源码。
            report: 回测报告 dict（可选，用于指导变异方向）。
            hint: 变异方向提示。

        Returns:
            ``MutationResult`` 含变异后源码 + diff + 费用统计。

        Raises:
            LLMError: LLM 调用失败。
            DiffApplyError: diff 无法应用。
        """
        user_prompt = build_user_prompt(current_source, report, hint)
        request = MutationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        try:
            response = await self.llm_client.mutate(request)
        except Exception as exc:
            raise LLMError(f"LLM 变异调用失败：{exc}") from exc

        raw_diff = response.content.strip()

        # 空 diff = LLM 认为无需改动
        if not raw_diff or not raw_diff.startswith("---"):
            return MutationResult(
                new_source=current_source,
                unified_diff=None,
                source_hash=sha256(current_source.encode()).hexdigest(),
                llm_cost_usd=response.cache_metrics.cost_usd,
                cache_hit_tokens=response.cache_metrics.cache_read_tokens,
            )

        try:
            new_source = apply_diff(current_source, raw_diff, max_fuzz=self.max_fuzz)
        except DiffApplyError as exc:
            # 带上 cost 信息，上层可决定是否计入成本
            raise DiffApplyError(
                str(exc),
                original=current_source,
                failed_diff=raw_diff,
            ) from exc

        return MutationResult(
            new_source=new_source,
            unified_diff=raw_diff,
            source_hash=sha256(new_source.encode()).hexdigest(),
            llm_cost_usd=response.cache_metrics.cost_usd,
            cache_hit_tokens=response.cache_metrics.cache_read_tokens,
        )
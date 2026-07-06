from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MutationRole(str, Enum):
    """prompt 角色 —— 决定 system_prompt 是否走 cache。"""

    MUTATE = "mutate"
    """策略变异 —— 5KB 静态 system prompt 可 cache。"""
    REVIEW = "review"
    """代码审查 —— 可能会不同 system prompt（暂时不 cache）。"""


@dataclass(slots=True, frozen=True)
class CacheMetrics:
    """单次 LLM 调用的缓存命中统计。

    从 Anthropic 响应头解析（``anthropic-ratelimit-*`` 与 ``cache-*`` 头）。
    """

    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        """按当前 Anthropic 定价估算费用（美元）。

        ADR-0014 cache 定价：cache_write = 1.25× input, cache_read = 0.1× input。
        参考：Claude Sonnet 4 input $3/Mtokens, output $15/Mtokens。
        """
        # 硬编码近期定价，不与实际账单绑定
        _INPUT_RATE = 3.0 / 1_000_000
        _OUTPUT_RATE = 15.0 / 1_000_000
        _CACHE_WRITE_MULT = 1.25
        _CACHE_READ_MULT = 0.10

        uncached = self.input_tokens * _INPUT_RATE
        cached_write = self.cache_write_tokens * _INPUT_RATE * _CACHE_WRITE_MULT
        cached_read = self.cache_read_tokens * _INPUT_RATE * _CACHE_READ_MULT
        output_cost = self.output_tokens * _OUTPUT_RATE
        return uncached + cached_write + cached_read + output_cost


@dataclass(slots=True, frozen=True)
class MutationRequest:
    """LLM 变异请求 —— 按 ADR-0014 分 cacheable 与 dynamic。

    ``system_prompt`` 是静态模板（~5KB），可缓存 5 分钟。
    ``user_prompt`` 每轮不同（含当前源码 + 回测报告 + hint），**不**缓存。
    """

    system_prompt: str
    """LLM 系统提示词 —— 静态、cacheable。"""
    user_prompt: str
    """本轮变异的具体上下文 —— 动态、不缓存。"""
    role: MutationRole = MutationRole.MUTATE
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass(slots=True, frozen=True)
class MutationResponse:
    """LLM 变异响应。"""

    content: str
    """LLM 返回的文本内容（应为 unified diff）。"""
    cache_metrics: CacheMetrics = field(default_factory=CacheMetrics)
    role: MutationRole = MutationRole.MUTATE
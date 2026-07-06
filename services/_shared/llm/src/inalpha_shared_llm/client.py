"""Anthropic SDK 异步封装 —— ADR-0014 cache 友好接口。

设计要点：

- ``mutate()`` 是核心入口，接收 ADR-0014 风格的 (system_prompt, user_prompt) 分体，
  system_prompt 走 prompt caching（前缀缓存）
- ``cache_control`` 加到 system block，让 Anthropic 自动管理缓存（约 5min TTL）
- ``CacheMetrics`` 从响应头解析 ``cache_creation_input_tokens`` /
  ``cache_read_input_tokens`` 字段
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .config import LLMSettings, get_llm_settings
from .types import CacheMetrics, MutationRequest, MutationResponse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMClient:
    """Anthropic LLM 客户端。"""

    settings: LLMSettings = field(default_factory=get_llm_settings)
    _client: Any | None = None  # 延迟初始化，避免 import time 建 client

    async def _ensure_client(self) -> Any:
        """延迟初始化 Anthropic async client。"""
        if self._client is None:
            # lazy import：只有调 LLM 时才触发 anthropic 包的 import
            from anthropic import AsyncAnthropic

            kwargs: dict[str, Any] = {}
            if self.settings.anthropic_api_key:
                kwargs["api_key"] = self.settings.anthropic_api_key
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    async def mutate(self, request: MutationRequest) -> MutationResponse:
        """执行一次 LLM 变异调用。

        Args:
            request: 含 system_prompt（cacheable）+ user_prompt（动态）+ 参数

        Returns:
            ``MutationResponse``，含 LLM 回复文本 + 缓存统计

        Raises:
            anthropic.APIError / anthropic.APITimeoutError: 底层 API 异常
        """
        client = await self._ensure_client()

        message = await client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            system=[
                {
                    "type": "text",
                    "text": request.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {"role": "user", "content": request.user_prompt},
            ],
            extra_headers={
                "anthropic-beta": "prompt-caching-2024-07-31",
            },
        )

        # 从消息模型提取 token 使用统计
        usage = message.usage

        cache_metrics = CacheMetrics(
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0),
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )

        # 合并 content blocks 为纯文本
        content = "".join(
            block.text for block in message.content if block.type == "text"
        )

        return MutationResponse(content=content, cache_metrics=cache_metrics)

    async def close(self) -> None:
        """关闭底层 HTTP client。"""
        if self._client is not None:
            await self._client.close()
            self._client = None


class MockLLMClient:
    """测试用的 mock LLM client —— 不接真实 API，返回预设响应。

    用于单元测试，验证上层代码（mutator / governor）在 LLM 正常/异常时的行为。
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: list[str] = responses or []
        self._call_count: int = 0

    async def mutate(self, request: MutationRequest) -> MutationResponse:
        if self._call_count >= len(self.responses):
            msg = f"MockLLMClient: 第 {self._call_count + 1} 次调用超出预设响应数 ({len(self.responses)})"
            raise RuntimeError(msg)
        resp = self.responses[self._call_count]
        self._call_count += 1
        return MutationResponse(content=resp)

    async def close(self) -> None:
        pass
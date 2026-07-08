"""通用 LLM 客户端 —— OpenAI-compat provider 封装。

设计要点：

- ``mutate()`` 是核心入口，接收 (system_prompt, user_prompt) 分体
- 走 ``openai.AsyncOpenAI`` SDK（DeepSeek / GLM-5.2 / OpenAI / Kimi / Zhipu 都是兼容接口）
- system prompt 作为 messages 数组的第一个 system role 消息
- CacheMetrics 从 openai usage 字段解析（与 Anthropic 的 cache 字段略有不同）
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
    """OpenAI-compat LLM 客户端。

    支持所有 OpenAI 兼容的服务：DeepSeek、GLM-5.2、OpenAI、Kimi、Zhipu、Ollama 等。
    """

    settings: LLMSettings = field(default_factory=get_llm_settings)
    _client: Any | None = None  # 延迟初始化

    async def _ensure_client(self) -> Any:
        """延迟初始化 AsyncOpenAI client。"""
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {}
            key = self.settings.effective_api_key
            if key:
                kwargs["api_key"] = key
            if self.settings.llm_base_url:
                kwargs["base_url"] = self.settings.llm_base_url
            if self.settings.llm_timeout_s:
                kwargs["timeout"] = float(self.settings.llm_timeout_s)
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def mutate(self, request: MutationRequest) -> MutationResponse:
        """执行一次 LLM 变异调用。

        Args:
            request: 含 system_prompt + user_prompt + 参数

        Returns:
            ``MutationResponse``，含 LLM 回复文本 + token 统计

        Raises:
            openai.APIError / openai.APITimeoutError: 底层 API 异常
        """
        client = await self._ensure_client()

        messages: list[dict[str, str]] = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]

        completion = await client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        # 从响应提取 token 使用统计
        usage = completion.usage

        cache_metrics = CacheMetrics(
            cache_read_tokens=0,  # OpenAI compat 无 Anthropic 风格的 cache
            cache_write_tokens=0,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

        # 合并 choices 文本
        content = completion.choices[0].message.content or ""

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
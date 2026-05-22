"""LLM 客户端封装 + Fake 实现。

设计：

- ``LLMClient`` 是个小 Protocol，只暴露 ``complete_json(system, user) -> dict``
- ``DeepSeekLLMClient`` 走 OpenAI Python SDK + ``response_format={"type":"json_object"}``
  让 DeepSeek 输出严格 JSON（DeepSeek 兼容 OpenAI 这个字段）
- ``FakeLLMClient`` 是 lookup-table：按 prompt 子串匹配返预设 dict
  → analyst / manager 单测不依赖网络

为什么不直接用 ``BaseChatModel`` 一类的库（langchain 等）：增加大量依赖、版本耦合，
本项目就 2 个 analyst + 1 manager 三处调用点，自己包一层就够。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from inalpha_shared.errors import InalphaError


class LLMError(InalphaError):
    code = "LLM_ERROR"
    status_code = 502


class LLMClient(Protocol):
    """所有 LLM client 必须实现：传 system + user prompt，返 JSON dict。

    不暴露 raw text 接口 —— analyst / manager 的输出必须是结构化 JSON 才好喂
    回 Pydantic 校验。需要 raw text 的场景（如调试）单独加 ``complete_text``。
    """

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        ...

    async def aclose(self) -> None:
        ...


# ────────────────────────────────────────────────────────────────────
# DeepSeek (OpenAI compat)
# ────────────────────────────────────────────────────────────────────


class DeepSeekLLMClient:
    """走 DeepSeek API（OpenAI 兼容）。

    用 openai-python SDK 是因为 DeepSeek 文档明确推荐这条路，且文件少。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeekLLMClient: api_key is required")
        # 延迟 import：测试不走这条路时不需要装 openai
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._model = model

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        try:
            r = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            raise LLMError(
                f"DeepSeek API call failed: {e}",
                code="LLM_PROVIDER_ERROR",
                details={"provider": "deepseek", "model": self._model},
            ) from e

        choices = r.choices
        if not choices:
            raise LLMError(
                "DeepSeek returned no choices",
                code="LLM_EMPTY_RESPONSE",
            )
        content = choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"DeepSeek did not return valid JSON: {e}",
                code="LLM_INVALID_JSON",
                details={"raw": content[:500]},
            ) from e
        if not isinstance(data, dict):
            raise LLMError(
                f"DeepSeek returned non-dict JSON: {type(data).__name__}",
                code="LLM_INVALID_JSON",
            )
        return data

    async def aclose(self) -> None:
        await self._client.close()


# ────────────────────────────────────────────────────────────────────
# Fake (tests)
# ────────────────────────────────────────────────────────────────────


class FakeLLMClient:
    """测试用 LLM —— 按 prompt 子串 / system role 选预设响应。

    用法（pytest fixture）::

        fake = FakeLLMClient({
            "technical analyst": {"stance": "bullish", "summary": "ema cross", ...},
            "research manager":   {"rating": "overweight", ...},
        })

    匹配逻辑：遍历 ``responses`` keys，**第一个 system prompt 包含该 key** 的命中。
    没命中抛 ``LLMError`` ——避免静默走错路径。
    """

    def __init__(
        self,
        responses: Mapping[str, dict[str, Any]] | None = None,
        *,
        on_call: Any = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def set_response(self, key: str, value: dict[str, Any]) -> None:
        """运行时改 / 加响应。"""
        self._responses[key] = value

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self._on_call is not None:
            await self._on_call(system=system, user=user)
        for key, resp in self._responses.items():
            if key.lower() in system.lower():
                return resp
        raise LLMError(
            "FakeLLMClient: no canned response matched the system prompt",
            code="LLM_FAKE_NO_MATCH",
            details={"system_prefix": system[:200]},
        )

    async def aclose(self) -> None:
        return None


# ────────────────────────────────────────────────────────────────────
# factory
# ────────────────────────────────────────────────────────────────────


def build_llm_client(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> LLMClient:
    """按 settings 构造 LLM client。

    ``provider``:
    - ``"deepseek"``：真 DeepSeek API（require api_key）
    - ``"fake"``：返空 FakeLLMClient —— 没注 response 就抛错，**不要在生产用**
    """
    p = provider.lower()
    if p == "deepseek":
        return DeepSeekLLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    if p == "fake":
        return FakeLLMClient()
    raise ValueError(f"unknown LLM provider {provider!r}; supported: 'deepseek' | 'fake'")

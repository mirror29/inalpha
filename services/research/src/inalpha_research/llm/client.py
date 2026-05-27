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

import asyncio
import json
import logging
import random
from collections.abc import Mapping
from typing import Any, Protocol

from inalpha_shared.errors import InalphaError

logger = logging.getLogger(__name__)


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

    并发与重试护栏（D-9）：

    - ``max_concurrent``：实例级 ``asyncio.Semaphore``。同一 client 被 5 analyst +
      Bull/Bear + manager 共享 → 限流放这里就够，不需要单独 wrapper
    - ``max_retries``：对 ``RateLimitError / APITimeoutError / InternalServerError``
      指数退避重试；其他异常（认证 / 400 / json parse）直接抛 ``LLMError`` 不重试
    - 退避公式：``base * 2^attempt + uniform(0, base*0.5)`` 抖动，避免雪崩同步
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout_seconds: float = 60.0,
        max_concurrent: int = 5,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
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
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds

    async def _call_with_retry(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Any:
        """实际 SDK 调用，对可重试错误指数退避。

        可重试：``RateLimitError``（429）/ ``APITimeoutError`` / ``InternalServerError``（5xx）。
        不重试：``AuthenticationError`` / ``BadRequestError`` / ``LLMError`` 等。
        """
        from openai import APITimeoutError, InternalServerError, RateLimitError

        retriable = (RateLimitError, APITimeoutError, InternalServerError)
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                return await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
            except retriable as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                backoff = self._retry_base_seconds * (2**attempt)
                jitter = random.uniform(0, self._retry_base_seconds * 0.5)
                sleep_s = backoff + jitter
                logger.warning(
                    "LLM retriable error (attempt %d/%d): %s; sleeping %.2fs",
                    attempt + 1,
                    self._max_retries + 1,
                    type(e).__name__,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)

        # 走到这说明可重试错误把次数用光
        assert last_err is not None
        raise LLMError(
            f"DeepSeek API call failed after {self._max_retries + 1} attempts: {last_err}",
            code="LLM_PROVIDER_ERROR",
            details={
                "provider": "deepseek",
                "model": self._model,
                "error_type": type(last_err).__name__,
            },
        ) from last_err

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        async with self._semaphore:
            try:
                r = await self._call_with_retry(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LLMError:
                # _call_with_retry 已经包过了，原样抛
                raise
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
    max_concurrent: int = 5,
    max_retries: int = 3,
    retry_base_seconds: float = 1.0,
) -> LLMClient:
    """按 settings 构造 LLM client。

    ``provider``:
    - ``"deepseek"``：真 DeepSeek API（require api_key）；新增 ``max_concurrent /
      max_retries / retry_base_seconds`` 由 ``ResearchSettings`` 控制护栏
    - ``"fake"``：返空 FakeLLMClient —— 没注 response 就抛错，**不要在生产用**；
      不接收护栏参数（测试不需要限流和退避）
    """
    p = provider.lower()
    if p == "deepseek":
        return DeepSeekLLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
        )
    if p == "fake":
        return FakeLLMClient()
    raise ValueError(f"unknown LLM provider {provider!r}; supported: 'deepseek' | 'fake'")

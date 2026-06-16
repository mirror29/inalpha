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


def _strip_code_fence(content: str) -> str:
    """剥掉 ```json ... ``` / ``` ... ``` markdown 围栏，返回内部内容。

    模型偶尔会把 JSON 包在代码块里（尤其上下文长时）；不剥会让 ``json.loads`` 直接失败。
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):  # 去掉首行 ``` 或 ```json
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):  # 去掉结尾 ```
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_response(content: str, *, provider: str, model: str) -> dict[str, Any]:
    """把 LLM 文本解析成 JSON dict，容错 markdown 围栏。

    解析失败（含被 ``max_tokens`` 截断的残缺 JSON、或夹杂散文）抛 ``LLMError``，
    **status_code=500**——这是"我方拿到了响应但没解析成功"，不是上游网关不可达。
    用 502（Bad Gateway）会误导调用方 / orchestrator agent 以为 provider 宕机，
    继而把"截断"叙述成"DeepSeek API 故障"（ADR-0037 调试记录）。
    """
    text = _strip_code_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(
            f"{provider} response was not valid JSON "
            f"(likely truncated at max_tokens or wrapped in prose): {e}",
            code="LLM_INVALID_JSON",
            status_code=500,
            details={"provider": provider, "model": model, "raw": content[:500]},
        ) from e
    if not isinstance(data, dict):
        raise LLMError(
            f"{provider} returned non-dict JSON: {type(data).__name__}",
            code="LLM_INVALID_JSON",
            status_code=500,
            details={"provider": provider, "model": model},
        )
    return data


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
        model: str = "deepseek-v4-pro",
        timeout_seconds: float = 60.0,
        max_concurrent: int = 5,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        provider_name: str = "deepseek",
    ) -> None:
        # provider_name 让 OpenAI-compat 家族（openai / kimi / zhipu / ollama）
        # 复用本类时，错误日志仍带正确的 provider 标识
        if not api_key:
            raise ValueError(f"{provider_name}: api_key is required")
        # 延迟 import：测试不走这条路时不需要装 openai
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._model = model
        self._provider_name = provider_name
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
            f"{self._provider_name} API call failed after {self._max_retries + 1} attempts: {last_err}",
            code="LLM_PROVIDER_ERROR",
            details={
                "provider": self._provider_name,
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
                    f"{self._provider_name} API call failed: {e}",
                    code="LLM_PROVIDER_ERROR",
                    details={"provider": self._provider_name, "model": self._model},
                ) from e

        choices = r.choices
        if not choices:
            raise LLMError(
                f"{self._provider_name} returned no choices",
                code="LLM_EMPTY_RESPONSE",
            )
        content = choices[0].message.content or ""
        return _parse_json_response(content, provider=self._provider_name, model=self._model)

    async def aclose(self) -> None:
        await self._client.close()


# ────────────────────────────────────────────────────────────────────
# Anthropic (Claude)
# ────────────────────────────────────────────────────────────────────


class AnthropicLLMClient:
    """走 Anthropic Messages API。

    与 DeepSeekLLMClient 的差异：

    - 用 ``anthropic.AsyncAnthropic`` SDK（不是 OpenAI 兼容）
    - 没有 native ``response_format=json_object``，靠 system prompt 强制 + 解析；
      我们走"system prompt 后缀注入 ``ONLY OUTPUT JSON``"的稳妥路径
    - 限流 / 重试与 DeepSeek 同构，把 anthropic 自家的异常类映射进来
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-4-8",
        timeout_seconds: float = 60.0,
        max_concurrent: int = 5,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("anthropic: api_key is required")
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds

    async def _call_with_retry(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> Any:
        from anthropic import APITimeoutError, InternalServerError, RateLimitError

        retriable = (RateLimitError, APITimeoutError, InternalServerError)
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                return await self._client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system + "\n\nIMPORTANT: respond with a single valid JSON object only.",
                    messages=[{"role": "user", "content": user}],
                )
            except retriable as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                backoff = self._retry_base_seconds * (2**attempt)
                jitter = random.uniform(0, self._retry_base_seconds * 0.5)
                sleep_s = backoff + jitter
                logger.warning(
                    "anthropic retriable error (attempt %d/%d): %s; sleeping %.2fs",
                    attempt + 1,
                    self._max_retries + 1,
                    type(e).__name__,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)

        assert last_err is not None
        raise LLMError(
            f"anthropic API call failed after {self._max_retries + 1} attempts: {last_err}",
            code="LLM_PROVIDER_ERROR",
            details={
                "provider": "anthropic",
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
        async with self._semaphore:
            try:
                r = await self._call_with_retry(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(
                    f"anthropic API call failed: {e}",
                    code="LLM_PROVIDER_ERROR",
                    details={"provider": "anthropic", "model": self._model},
                ) from e

        # Anthropic 返回 content blocks list；我们只取第一块的 text
        blocks = r.content
        if not blocks:
            raise LLMError(
                "anthropic returned no content blocks", code="LLM_EMPTY_RESPONSE"
            )
        text = getattr(blocks[0], "text", "") or ""
        return _parse_json_response(text, provider="anthropic", model=self._model)

    async def aclose(self) -> None:
        await self._client.close()


# ────────────────────────────────────────────────────────────────────
# Gemini (Google)
# ────────────────────────────────────────────────────────────────────


class GeminiLLMClient:
    """走 Google Gemini API（google-genai SDK）。

    用 ``response_mime_type="application/json"`` 强制 JSON 输出，效果接近
    OpenAI 的 json_object 模式。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3-pro",
        timeout_seconds: float = 60.0,
        max_concurrent: int = 5,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
    ) -> None:
        if not api_key:
            raise ValueError("gemini: api_key is required")
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds

    async def _call_with_retry(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> Any:
        # google-genai 的异常类不如 anthropic/openai 分得细；通过 status code 字段判断
        from google.genai import errors as genai_errors

        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=user,
                    config={
                        "system_instruction": system,
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                        "response_mime_type": "application/json",
                    },
                )
            except genai_errors.APIError as e:
                last_err = e
                # 429 / 500-599 可重试；其他直接抛
                code = getattr(e, "code", 0)
                if code != 429 and not (500 <= code < 600):
                    raise
                if attempt >= self._max_retries:
                    break
                backoff = self._retry_base_seconds * (2**attempt)
                jitter = random.uniform(0, self._retry_base_seconds * 0.5)
                sleep_s = backoff + jitter
                logger.warning(
                    "gemini retriable error (attempt %d/%d): code=%s; sleeping %.2fs",
                    attempt + 1,
                    self._max_retries + 1,
                    code,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)

        assert last_err is not None
        raise LLMError(
            f"gemini API call failed after {self._max_retries + 1} attempts: {last_err}",
            code="LLM_PROVIDER_ERROR",
            details={
                "provider": "gemini",
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
        async with self._semaphore:
            try:
                r = await self._call_with_retry(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(
                    f"gemini API call failed: {e}",
                    code="LLM_PROVIDER_ERROR",
                    details={"provider": "gemini", "model": self._model},
                ) from e

        text = getattr(r, "text", "") or ""
        return _parse_json_response(text, provider="gemini", model=self._model)

    async def aclose(self) -> None:
        # google-genai 没有显式 close
        return None


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


# 各 provider 默认 base_url + 默认模型（OpenAI-compat 家族）。
# 模型选型原则（2026-05 更新）：每家当前主流旗舰。
# 详见 packages/orchestration/src/mastra/llm/provider.ts DEFAULT_MODELS 注释表。
_OPENAI_COMPAT_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider -> (default_base_url, default_model)
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-v4-pro"),
    "openai": ("https://api.openai.com/v1", "gpt-5.5"),
    "kimi": ("https://api.moonshot.cn/v1", "kimi-k2.6"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
    "ollama": ("http://localhost:11434/v1", "llama4"),
}

# 非 OpenAI-compat provider 的默认模型
_NATIVE_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "gemini": "gemini-3-pro",
}

SUPPORTED_PROVIDERS = (
    "deepseek",
    "anthropic",
    "openai",
    "gemini",
    "kimi",
    "zhipu",
    "ollama",
    "fake",
)


def _with_language_directive(system: str, language: str) -> str:
    """在 system prompt 末尾追加输出语言指令（recency 位置，最显眼）。

    analyst / researcher / manager 的 system prompt 都是中文写的，默认模型会跟着
    输出中文（Fix C）。这里强制所有自然语言字段用用户语言；JSON key / ticker / 数值不动。
    """
    return (
        f"{system}\n\n[OUTPUT LANGUAGE] Write every natural-language string value in "
        f"your JSON response (summary, rationale, argument, reasoning, recommendation, "
        f"thesis, etc.) in {language}. Do NOT translate or change JSON keys, tickers, "
        f"symbols, numbers, or factor IDs."
    )


class LanguageScopedClient:
    """包装任意 LLMClient，对每次 ``complete_json`` 的 system 注入输出语言指令。

    per-request 构造（deep_dive 路由按 ``req.language`` 包装），不共享可变状态、并发
    安全；``aclose`` 透传内层 client。language 为空时调用方不应包装（保持模型默认行为）。
    """

    def __init__(self, inner: LLMClient, language: str) -> None:
        self._inner = inner
        self._language = language

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        return await self._inner.complete_json(
            system=_with_language_directive(system, self._language),
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_llm_client(
    *,
    provider: str,
    api_key: str,
    base_url: str = "",
    model: str = "",
    timeout_seconds: float = 60.0,
    max_concurrent: int = 5,
    max_retries: int = 3,
    retry_base_seconds: float = 1.0,
) -> LLMClient:
    """按 provider 构造 LLM client。

    支持的 provider（与 README §Recommended Models 对齐）：

    - **OpenAI-compatible 家族**（共用 ``DeepSeekLLMClient`` 实现，传不同 base_url）：
      ``deepseek`` / ``openai`` / ``kimi`` / ``zhipu`` / ``ollama``
    - **原生 SDK**：``anthropic`` / ``gemini``
    - ``fake``：测试用 mock；不需要 api_key

    ``base_url`` / ``model`` 留空时，按 provider 默认值填充。Ollama 需要本地
    服务起好（默认 ``http://localhost:11434/v1``）。
    """
    p = provider.lower()

    if p == "fake":
        return FakeLLMClient()

    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"unknown LLM provider {provider!r}; "
            f"supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    # OpenAI-compat 家族
    if p in _OPENAI_COMPAT_DEFAULTS:
        default_base, default_model = _OPENAI_COMPAT_DEFAULTS[p]
        return DeepSeekLLMClient(
            api_key=api_key or ("ollama" if p == "ollama" else ""),
            base_url=base_url or default_base,
            model=model or default_model,
            timeout_seconds=timeout_seconds,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            provider_name=p,
        )

    if p == "anthropic":
        return AnthropicLLMClient(
            api_key=api_key,
            model=model or _NATIVE_DEFAULTS["anthropic"],
            timeout_seconds=timeout_seconds,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
        )

    if p == "gemini":
        return GeminiLLMClient(
            api_key=api_key,
            model=model or _NATIVE_DEFAULTS["gemini"],
            timeout_seconds=timeout_seconds,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
        )

    # 不可达：上面 if p not in SUPPORTED_PROVIDERS 已抛
    raise AssertionError(f"unreachable provider branch: {p}")

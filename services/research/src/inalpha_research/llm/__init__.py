"""LLM 抽象层。

支持的 provider（``ResearchSettings.llm_provider`` 控制）：

- ``DeepSeekLLMClient``：OpenAI-compat 家族共用实现
  （deepseek / openai / kimi / zhipu / ollama 都复用本类，仅 base_url + model 不同）
- ``AnthropicLLMClient``：Claude 原生 SDK
- ``GeminiLLMClient``：Google Gemini 原生 SDK
- ``FakeLLMClient``：测试用，按 prompt 关键字返预设响应

具体路由见 ``build_llm_client(provider=...)``。
"""
from .client import (
    SUPPORTED_PROVIDERS,
    AnthropicLLMClient,
    DeepSeekLLMClient,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
    build_llm_client,
)

__all__ = [
    "SUPPORTED_PROVIDERS",
    "AnthropicLLMClient",
    "DeepSeekLLMClient",
    "FakeLLMClient",
    "GeminiLLMClient",
    "LLMClient",
    "build_llm_client",
]

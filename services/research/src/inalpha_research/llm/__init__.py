"""LLM 抽象层。

提供两个实现：

- ``DeepSeekLLMClient``：走 OpenAI 兼容 API（DeepSeek 同协议）
- ``FakeLLMClient``：测试用，按 prompt 关键字返预设响应

切换由 ``ResearchSettings.llm_provider`` 控制。
"""
from .client import DeepSeekLLMClient, FakeLLMClient, LLMClient, build_llm_client

__all__ = ["DeepSeekLLMClient", "FakeLLMClient", "LLMClient", "build_llm_client"]

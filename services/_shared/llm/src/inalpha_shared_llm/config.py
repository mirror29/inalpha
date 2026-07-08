"""LLM 客户端通用配置。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class LLMSettings(BaseSettings):
    """LLM 客户端通用配置。

    环境变量前缀无强制，但建议 ``LLM_*`` 命名空间到对接的 service settings 中。
    """

    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="LLM API key。留空 = 用默认凭证链（环境变量 / .env）。",
    )
    deepseek_api_key: str = Field(
        default="",
        alias="DEEPSEEK_API_KEY",
        description="DeepSeek API key（fallback 到 LLM_API_KEY）。",
    )

    @property
    def effective_api_key(self) -> str:
        """优先 DEEPSEEK_API_KEY，再 LLM_API_KEY。"""
        return self.deepseek_api_key or self.llm_api_key
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        alias="LLM_BASE_URL",
        description="OpenAI-compatible API base URL。默认 DeepSeek。",
    )
    llm_model: str = Field(
        default="deepseek-chat",
        alias="LLM_MODEL",
        description="LLM 模型 ID。",
    )
    llm_timeout_s: int = Field(
        default=120,
        alias="LLM_TIMEOUT_S",
        ge=1,
        le=600,
        description="单次 LLM 调用的超时秒数。",
    )
    llm_max_tokens: int = Field(
        default=4096,
        alias="LLM_MAX_TOKENS",
        ge=256,
        le=16384,
        description="LLM 回复最大 token 数（输出限制）。",
    )


@lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    return LLMSettings()  # type: ignore[call-arg]
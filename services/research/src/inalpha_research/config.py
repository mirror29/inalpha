"""research service 专属 settings。

继承 ``inalpha_shared.Settings``，加 LLM provider 与 data-service URL。
"""
from __future__ import annotations

from functools import lru_cache

from inalpha_shared.config import Settings as BaseSettings
from pydantic import Field


class ResearchSettings(BaseSettings):
    """research service 完整 settings。"""

    service_name: str = Field(default="research", alias="SERVICE_NAME")

    data_service_url: str = Field(
        default="http://localhost:8001",
        alias="DATA_SERVICE_URL",
        description="data-service 的 base URL，analyst 拉行情用。",
    )

    research_service_port: int = Field(default=8003, alias="RESEARCH_SERVICE_PORT")

    # ─── LLM ─────────────────────────────────────────────────────────
    llm_provider: str = Field(
        default="deepseek",
        alias="LLM_PROVIDER",
        description="LLM provider id；目前只支持 'deepseek'，'fake' 走 mock（测试用）",
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        alias="LLM_BASE_URL",
        description="OpenAI-compatible base URL；DeepSeek = api.deepseek.com/v1",
    )
    llm_model: str = Field(
        default="deepseek-chat",
        alias="LLM_MODEL",
        description="模型名；DeepSeek 用 deepseek-chat / deepseek-reasoner",
    )
    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="LLM provider 的 API key；fake provider 时可空",
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        alias="LLM_TIMEOUT_SECONDS",
        description="单次 LLM 调用超时（秒）",
    )

    # ─── Debate ──────────────────────────────────────────────────────
    max_debate_rounds: int = Field(
        default=1,
        ge=0,
        le=5,
        alias="RESEARCH_MAX_DEBATE_ROUNDS",
        description="Bull/Bear 辩论轮数；每轮 Bull 一次 + Bear 一次。"
        "0 = 跳过辩论（runner 直接 analyst→manager，保留旧 D-8c 行为）。"
        "默认 1（同 TradingAgents），>1 会成倍增加 LLM 成本",
    )


@lru_cache(maxsize=1)
def get_research_settings() -> ResearchSettings:
    return ResearchSettings()  # type: ignore[call-arg]

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
        description="LLM provider id；deepseek / anthropic / openai / gemini / "
        "kimi / zhipu / ollama / fake。详见 llm/client.py SUPPORTED_PROVIDERS",
    )
    llm_base_url: str = Field(
        default="",
        alias="LLM_BASE_URL",
        description="OpenAI-compatible base URL；留空时由 build_llm_client 按 provider 选默认值",
    )
    llm_model: str = Field(
        default="",
        alias="LLM_MODEL",
        description="模型名；留空时由 build_llm_client 按 provider 选默认（详见 README §Recommended Models）",
    )
    # 通用 key（兼容旧 .env 写法 LLM_API_KEY=xxx）；优先级低于 provider-specific
    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="通用 LLM API key（旧字段，兼容保留）。"
        "新写法请用 {PROVIDER}_API_KEY，例如 ANTHROPIC_API_KEY / OPENAI_API_KEY",
    )
    # provider-specific keys（新写法，与 .env.example 对齐）
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    kimi_api_key: str = Field(default="", alias="KIMI_API_KEY")
    zhipu_api_key: str = Field(default="", alias="ZHIPU_API_KEY")
    ollama_base_url: str = Field(
        default="http://localhost:11434/v1",
        alias="OLLAMA_BASE_URL",
        description="Ollama 本地服务 base URL（不需要 api_key）",
    )

    @property
    def effective_api_key(self) -> str:
        """按 llm_provider 选对应的 API key；fallback 到通用 LLM_API_KEY。

        优先级：``{provider}_API_KEY`` > ``LLM_API_KEY``。
        Ollama 不需要 key，返空。
        """
        provider_key = {
            "deepseek": self.deepseek_api_key,
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "kimi": self.kimi_api_key,
            "zhipu": self.zhipu_api_key,
            "ollama": "",  # ollama 不需要 key
            "fake": "",
        }.get(self.llm_provider.lower(), "")
        return provider_key or self.llm_api_key

    @property
    def effective_base_url(self) -> str:
        """ollama 的 base url 单独读 OLLAMA_BASE_URL；其他 provider 用 LLM_BASE_URL（可空）。"""
        if self.llm_provider.lower() == "ollama":
            return self.ollama_base_url
        return self.llm_base_url
    llm_timeout_seconds: float = Field(
        default=60.0,
        alias="LLM_TIMEOUT_SECONDS",
        description="单次 LLM 调用超时（秒）",
    )
    llm_max_concurrent: int = Field(
        default=5,
        ge=1,
        le=50,
        alias="LLM_MAX_CONCURRENT",
        description="单个 LLM client 实例的并发上限（asyncio.Semaphore）。"
        "Deep dive 5 analyst + Bull/Bear + manager 共享同一 client，默认 5 不阻塞单链路；"
        "D-9 swarm grid×N 会把多个 deep dive 串起来跑，超额时第 N+1 个调用排队等放行。",
    )
    llm_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        alias="LLM_MAX_RETRIES",
        description="LLM 调用因可重试错误（RateLimitError / APITimeoutError / InternalServerError）"
        "失败时的最大重试次数。0 = 不重试。",
    )
    llm_retry_base_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        alias="LLM_RETRY_BASE_SECONDS",
        description="指数退避基础间隔（秒），实际退避 = base * 2^attempt + jitter。",
    )

    # ─── Debate ──────────────────────────────────────────────────────
    max_debate_rounds: int = Field(
        default=0,
        ge=0,
        le=5,
        alias="RESEARCH_MAX_DEBATE_ROUNDS",
        description="Bull/Bear 辩论轮数；每轮 Bull 一次 + Bear 一次。"
        "0 = 跳过辩论（runner 直接 analyst→manager，保留旧 D-8c 行为）。"
        "默认 0（debate 新增 ~120s 串行 LLM 开销，MVP 研究不需要；需要时设 1）",
    )


@lru_cache(maxsize=1)
def get_research_settings() -> ResearchSettings:
    return ResearchSettings()  # type: ignore[call-arg]

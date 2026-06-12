"""research service 专属 settings。

继承 ``inalpha_shared.Settings``，加 LLM provider 与 data-service URL。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

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

    factor_service_url: str = Field(
        default="http://localhost:8004",
        alias="FACTOR_SERVICE_URL",
        description="factor-service base URL，technical analyst 取有效因子快照用。"
        "不可达时 analyst 降级回旧的指标快照，不阻断 deep_dive。",
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
        default=8,
        ge=1,
        le=50,
        alias="LLM_MAX_CONCURRENT",
        description="单个 LLM client 实例的并发上限（asyncio.Semaphore）。"
        "Deep dive 6 analyst（D-10 加 valuation）+ Bull/Bear + manager 共享同一 client，"
        "默认 8 让 6 个核心 analyst 全并行 + 留 headroom（再高徒增 provider 限流/502 风险）；"
        "opt-in personas 把 analyst 数推高时超额者排队，不阻塞主链路。"
        "D-9 swarm grid×N 串多个 deep dive 时，超额调用排队等放行。",
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
        "默认 0（debate 新增串行 LLM 开销，MVP 研究不需要；需要时设 1）。"
        "≥2 轮时第 1 轮开场并行（见 debate.run_debate），后续轮串行反驳。",
    )
    debate_max_tokens: int = Field(
        default=600,
        ge=128,
        le=4096,
        alias="RESEARCH_DEBATE_MAX_TOKENS",
        description="辩论单次发言的输出 token 上限（#2 优化）。LLM 延迟≈输出长度，"
        "对喷论证 ~600 token 足够，比默认 2048 明显更快、论证更紧凑。",
    )
    debate_timeout_seconds: float = Field(
        default=90.0,
        ge=5.0,
        le=600.0,
        alias="RESEARCH_DEBATE_TIMEOUT_SECONDS",
        description="整个辩论阶段的总时限（#4 韧性）。超时返回**已完成**的部分 debate_log，"
        "不抛错、不拖满下游 tool 预算；配合 manager 兜底保证 deep_dive 端到端不挂。",
    )
    debate_trigger: Literal["contested", "always"] = Field(
        default="contested",
        alias="RESEARCH_DEBATE_TRIGGER",
        description="research-hub #6：debate 触发策略。contested = analyst briefs 出现"
        "有信心的多空对立才辩（一致则跳过省 token，判定见 debate.assess_disagreement）；"
        "always = 只要 max_debate_rounds>0 就辩（保留旧 D-9 行为）。"
        "判定结果原样落 ResearchPlan.debate_trigger 供复盘。",
    )
    debate_risk_enabled: bool = Field(
        default=True,
        alias="RESEARCH_DEBATE_RISK_ENABLED",
        description="research-hub #6：辩论是否加入 Risk 风险官第三方（每轮在 Bull/Bear "
        "之后压测双方论点）。关掉退回 Bull/Bear 两方制，省 1/3 辩论 LLM 开销。",
    )
    debate_convergence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        alias="RESEARCH_DEBATE_CONVERGENCE_THRESHOLD",
        description="research-hub #6：软早停阈值。从第 2 轮起 Bull/Bear 论证与各自上轮"
        "的词汇 Jaccard 重合度都 ≥ 此值 = 没有新论点，提前结束。1.0 = 实际禁用"
        "（只有逐字相同才触发）。注：软早停仅对 max_debate_rounds >= 3 有实际效果"
        "——早停只在「还有下一轮可省」时检查，1~2 轮没有可省空间，本配置无作用。",
    )


@lru_cache(maxsize=1)
def get_research_settings() -> ResearchSettings:
    return ResearchSettings()  # type: ignore[call-arg]

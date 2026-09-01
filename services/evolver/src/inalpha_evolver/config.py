from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class EvolverSettings(BaseSettings):
    """Evolver 服务配置。

    环境变量建议前缀 ``EVOLVER_*``。
    """

    # ---- DB ----
    database_url: str = Field(
        default="",
        alias="DATABASE_URL",
        description="PostgreSQL 连接串。",
    )

    evolver_pool_size: int = Field(
        default=5,
        alias="EVOLVER_POOL_SIZE",
        ge=2,
        le=20,
        description="DB 连接池大小。",
    )

    # ---- Job 执行 ----
    evolver_job_timeout_s: int = Field(
        default=300,
        alias="EVOLVER_JOB_TIMEOUT_S",
        ge=30,
        le=3600,
        description="单个回测任务的超时秒数（传参给 subprocess_runner）。",
    )

    evolver_run_timeout_s: int = Field(
        default=1200,
        alias="EVOLVER_RUN_TIMEOUT_S",
        ge=60,
        le=14400,
        description="整个单代 run 的总墙钟期限。",
    )

    evolver_job_mem_gb: int = Field(
        default=2,
        alias="EVOLVER_JOB_MEM_GB",
        ge=1,
        le=8,
        description="单个回测子进程的内存上限（GB）。",
    )
    evolver_max_running_runs: int = Field(
        default=1,
        alias="EVOLVER_MAX_RUNNING_RUNS",
        ge=1,
        le=8,
    )
    evolver_queue_timeout_s: int = Field(
        default=86400,
        alias="EVOLVER_QUEUE_TIMEOUT_S",
        ge=60,
        le=86400,
        description="queued run 最长等待时间；必须短于凭据 grant 的 30 小时有效期。",
    )
    evolver_account_active_limit: int = Field(
        default=2,
        alias="EVOLVER_ACCOUNT_ACTIVE_LIMIT",
        ge=1,
        le=20,
    )
    data_service_url: str = Field(
        default="http://127.0.0.1:8001",
        alias="DATA_SERVICE_URL",
    )
    evolver_data_timeout_s: int = Field(
        default=60,
        alias="EVOLVER_DATA_TIMEOUT_S",
        ge=5,
        le=300,
        description="E2 冻结行情与事件快照预检超时。",
    )
    evolver_credential_timeout_s: int = Field(
        default=60,
        alias="EVOLVER_CREDENTIAL_TIMEOUT_S",
        ge=5,
        le=300,
        description="Dashboard owner LLM 凭据兑换超时。",
    )
    dashboard_service_url: str = Field(
        default="http://127.0.0.1:3001",
        alias="DASHBOARD_SERVICE_URL",
    )
    evolver_llm_timeout_s: int = Field(
        default=120,
        alias="EVOLVER_LLM_TIMEOUT_S",
        ge=1,
        le=600,
    )
    jwt_secret: str = Field(default="dev-secret", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    service_token_ttl_s: int = Field(default=3600, ge=60, le=86400)
    evolution_credential_public_key_b64: str = Field(
        default="",
        alias="EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64",
        description="Orchestration Ed25519 签名公钥（SPKI DER base64）。",
    )
    event_evolution_enabled: bool = Field(default=False, alias="EVENT_EVOLUTION_ENABLED")
    """E2 campaign API/dispatcher feature flag；E1 run 不受影响。"""

    campaign_lease_ttl_s: int = Field(
        default=90,
        ge=30,
        le=600,
        alias="CAMPAIGN_LEASE_TTL_S",
        description="E2 campaign worker lease 与 fencing token 续租周期基准。",
    )
    campaign_max_concurrent: int = Field(
        default=1,
        ge=1,
        le=8,
        alias="CAMPAIGN_MAX_CONCURRENT",
        description="单个 Evolver 进程同时执行的 campaign 上限。",
    )

    # ---- LLM ----
    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="LLM API key。留空 = 用默认凭证链。",
    )

    deepseek_api_key: str = Field(
        default="",
        alias="DEEPSEEK_API_KEY",
        description="DeepSeek API key（优先于 LLM_API_KEY）。",
    )

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

    @property
    def effective_llm_api_key(self) -> str:
        """优先读 DEEPSEEK_API_KEY，fallback 到 LLM_API_KEY。"""
        return self.deepseek_api_key or self.llm_api_key

    # ---- 演化默认值 ----
    default_universe: list[str] = Field(
        default_factory=lambda: ["BTCUSDT"],
        alias="DEFAULT_UNIVERSE",
        description="默认回测宇宙。逗号分隔字符串或 JSON 数组。",
    )

    @field_validator("default_universe", mode="before")
    @classmethod
    def _parse_universe(cls, v: object) -> list[str]:
        if isinstance(v, str):
            if v.startswith("["):
                import json

                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return v
        return ["BTCUSDT"]

    default_period_from: str = Field(
        default="2025-01-01",
        alias="DEFAULT_PERIOD_FROM",
        description="默认回测起始日期（YYYY-MM-DD）。",
    )

    default_period_to: str = Field(
        default="2025-12-31",
        alias="DEFAULT_PERIOD_TO",
        description="默认回测截止日期（YYYY-MM-DD）。",
    )

    default_timeframe: str = Field(
        default="1h",
        alias="DEFAULT_TIMEFRAME",
        pattern=r"^\d+[mhdw]$",
        description="默认 timeframe（如 1h, 4h, 1d）。",
    )

    default_initial_cash: float = Field(
        default=10000.0,
        alias="DEFAULT_INITIAL_CASH",
        ge=100,
        description="默认初始本金（USD）。",
    )

    # ---- 服务 ----
    service_name: str = "inalpha-evolver"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_evolver_settings() -> EvolverSettings:
    return EvolverSettings()  # type: ignore[call-arg]

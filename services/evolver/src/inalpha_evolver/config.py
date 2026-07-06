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

    evolver_job_mem_gb: int = Field(
        default=2,
        alias="EVOLVER_JOB_MEM_GB",
        ge=1,
        le=8,
        description="单个回测子进程的内存上限（GB）。",
    )

    # ---- LLM ----
    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="LLM API key。留空 = 用默认凭证链。",
    )

    llm_model: str = Field(
        default="claude-sonnet-4-20250514",
        alias="LLM_MODEL",
        description="LLM 模型 ID。",
    )

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
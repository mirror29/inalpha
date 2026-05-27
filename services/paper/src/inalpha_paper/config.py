"""paper service 专属 settings。

继承 ``inalpha_shared.Settings``，加 ``DATA_SERVICE_URL`` 字段（跨服务调用 data 用）
+ Swarm S1（ADR-0025）的 ProcessPool 配置。
"""
from __future__ import annotations

import os
from functools import lru_cache

from inalpha_shared.config import Settings as BaseSettings
from pydantic import Field


def _default_pool_size() -> int:
    """默认 worker 数：``min(os.cpu_count() - 1, 6)``。

    上限 6 是为了 M 系列 P/E 核混搭场景：os.cpu_count 返物理+逻辑总数，
    给 BLAS / NumPy 子线程留余量，避免反而互相挤。
    """
    cpu = os.cpu_count() or 2
    return min(max(cpu - 1, 1), 6)


class PaperSettings(BaseSettings):
    """paper service 完整 settings。"""

    service_name: str = Field(default="paper", alias="SERVICE_NAME")

    data_service_url: str = Field(
        default="http://localhost:8001",
        alias="DATA_SERVICE_URL",
        description="data-service 的 base URL，paper 拉 K 线时走这里。",
    )

    paper_service_port: int = Field(default=8002, alias="PAPER_SERVICE_PORT")

    # ─── Swarm S1（ADR-0025）·  ProcessPool 配置 ─────────────────────

    pool_size: int = Field(
        default_factory=_default_pool_size,
        alias="PAPER_POOL_SIZE",
        ge=1,
        le=64,
        description="backtest ProcessPool worker 数；默认 min(CPU-1, 6)。",
    )
    job_timeout_s: int = Field(
        default=180,
        alias="PAPER_JOB_TIMEOUT_S",
        ge=1,
        le=3600,
        description="单 backtest job CPU 软上限秒数（RLIMIT_CPU 软值）。硬值 = 软 + 20。",
    )
    job_mem_gb: float = Field(
        default=2.0,
        alias="PAPER_JOB_MEM_GB",
        gt=0.0,
        le=64.0,
        description="单 backtest job RLIMIT_DATA 上限（GB）。macOS 上 mmap 仍可绕开。",
    )

    # ─── D-9 / D-9.1a · RiskEngine HTTP 接入（ADR-0006 / issue #3 + #8）─

    risk_engine_enabled: bool = Field(
        default=True,
        alias="INALPHA_RISK_ENGINE_ENABLED",
        description="HTTP 下单链路（/orders/submit + /plans/{id}/execute）是否过 RiskGuard 拦截。"
        "false → 退化为 pass-through，方便运维临时关闭风控（如数据迁移期）。",
    )
    risk_rules_config_path: str = Field(
        default="configs/risk_rules.toml",
        alias="INALPHA_RISK_RULES_CONFIG",
        description="risk_rules.toml 路径，相对 paper service 工作目录或绝对路径。"
        "文件缺失 / 解析失败 → lifespan log error 后 fail-open（不阻塞服务起步）。",
    )


@lru_cache(maxsize=1)
def get_paper_settings() -> PaperSettings:
    return PaperSettings()  # type: ignore[call-arg]

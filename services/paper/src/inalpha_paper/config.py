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

    # ─── D-11 · live runner（issue #1）─────────────────────────────────

    live_poll_interval_s: int = Field(
        default=0,
        alias="INALPHA_LIVE_POLL_INTERVAL_S",
        ge=0,
        le=3600,
        description="live runner 轮询周期（秒）。0 = 按 timeframe 自动推导（默认）；"
        ">0 时覆盖（测试 / 调试可调快）。",
    )
    live_max_error_streak: int = Field(
        default=5,
        alias="INALPHA_LIVE_MAX_ERROR_STREAK",
        ge=1,
        le=100,
        description="live runner 连续出错多少次后置 errored 停跑。",
    )
    live_max_running_runs_per_account: int = Field(
        default=10,
        alias="INALPHA_LIVE_MAX_RUNNING_RUNS_PER_ACCOUNT",
        ge=1,
        le=1000,
        description="单账户同时 running 的 live run 上限（资源软护栏 issue #36.2）。"
        "每个 run 一个长驻 asyncio task + 周期打 data /bars；超上限 start 返 429，"
        "防单用户起任意多 run 打爆事件循环 / 放大对 data 的请求。",
    )
    live_runner_token_ttl_s: int = Field(
        default=300,
        alias="INALPHA_LIVE_RUNNER_TOKEN_TTL_S",
        ge=30,
        le=3600,
        description="live runner 自签 service JWT 的有效期（秒），调 data /bars 用。",
    )
    live_warmup_bars: int = Field(
        default=200,
        alias="INALPHA_LIVE_WARMUP_BARS",
        ge=0,
        le=2000,
        description="live runner 启动时拉多少根历史 bar 预热策略指标（0 = 不预热）。"
        "让需要 lookback 的策略 start 后即有指标状态，不必空跑几十根实时 bar。",
    )
    live_runner_require_risk_guard: bool = Field(
        default=True,
        alias="INALPHA_LIVE_RUNNER_REQUIRE_RISK_GUARD",
        description="live runner 是否要求风控可用才起跑（**自动化路径 fail-closed**）。"
        "默认 true：风控不可用（risk_engine_enabled=false / TOML 加载失败 → factory=None）时"
        "拒绝起跑并置 errored——无人值守的自动下单循环不应在零风控下运行。"
        "显式置 false 可放行（如本地无风控调试），此时 run 的 error_log 会留一条醒目告警。",
    )
    live_runner_resume_on_startup: bool = Field(
        default=True,
        alias="INALPHA_LIVE_RUNNER_RESUME_ON_STARTUP",
        description="服务启动时是否自动 resume 残留 running run（issue #46）。默认 true："
        "重建 session（DB 持仓 + 预热指标）续跑，而非把它们判死——live runner 设计目标是"
        "长驻，每次部署不该让所有模拟盘 run 集体阵亡。置 false 回旧行为（残留全标 errored）。"
        "单实例 MVP 安全；多副本横向扩展前需先做 runner_instance_id 作用域（#38.1）。",
    )
    live_runner_auto_stop_on_circuit_break: bool = Field(
        default=True,
        alias="INALPHA_LIVE_RUNNER_AUTO_STOP_ON_CIRCUIT_BREAK",
        description="账户级风控锁（global scope：MaxDrawdown / StoplossGuard 熔断）触发时是否"
        "auto-stop 该 run（issue #44）。默认 true：策略打穿账户回撤上限 = 终态事件，"
        "停机置 stopped + error_log 记因，让人复核后再决定是否重启；否则 run 会在锁期内"
        "（数小时）持续空轮询 + 每根 bar 被拒，沦为僵尸 run。置 false 维持旧行为（继续跑）。",
    )


@lru_cache(maxsize=1)
def get_paper_settings() -> PaperSettings:
    return PaperSettings()  # type: ignore[call-arg]

"""paper service FastAPI 入口。

启动：``uvicorn inalpha_paper.main:app --port 8002``
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from inalpha_shared import (
    close_pool,
    configure_logging,
    init_pool,
    install_error_handler,
    install_request_logging,
)
from inalpha_shared.db import get_conn

from . import __version__
from .api import (
    backtest,
    health,
    orders,
    risk,
    strategies,
    strategy_candidates,
    strategy_runs,
    trade_plans,
)
from .config import get_paper_settings
from .engine.pool import init_pool as init_backtest_pool
from .engine.pool import shutdown_pool as shutdown_backtest_pool
from .execution.risk_guard_factory import RiskGuardFactory
from .execution.risk_rules import load_risk_rules_config
from .execution.risk_rules.market_calendar import RoutingCalendar
from .live_runner import LiveRunnerManager
from .storage import strategy_runs as runs_store

_settings = get_paper_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)
_logger = logging.getLogger(__name__)


def _resolve_config_path() -> Path | None:
    """从 settings 读 risk_rules.toml 路径，相对路径先工作目录后包根 fallback。

    sync 函数 —— 让 async ``_build_risk_guard_factory`` 不直接做 Path IO（避开 ASYNC240）。
    """
    cfg_path = Path(_settings.risk_rules_config_path)
    if cfg_path.is_absolute():
        return cfg_path if cfg_path.exists() else None
    if cfg_path.exists():
        return cfg_path.resolve()
    pkg_root = Path(__file__).resolve().parent.parent.parent
    candidate = pkg_root / _settings.risk_rules_config_path
    return candidate if candidate.exists() else None


async def _build_risk_guard_factory(pool: object) -> RiskGuardFactory | None:
    """读 TOML + 构造 RiskGuardFactory。失败 → log error + 返 None（fail-open）。

    D-9.1a 起 factory 按 caller account_id 派生独立 RiskGuard 实例（LRU cache），
    替代 D-9 demo 模式（``INALPHA_RISK_DEMO_ACCOUNT_SUB`` 绑定单 account）。
    """
    if not _settings.risk_engine_enabled:
        _logger.warning(
            "RiskGuard disabled (INALPHA_RISK_ENGINE_ENABLED=false) — HTTP 下单链路"
            "不过风控拦截，仅推荐运维窗口期使用"
        )
        return None

    cfg_path = _resolve_config_path()
    if cfg_path is None:
        _logger.error(
            "RiskGuardFactory: risk_rules.toml 未找到 (path=%s) — fail-open，"
            "HTTP 下单不过风控",
            _settings.risk_rules_config_path,
        )
        return None

    try:
        cfg = load_risk_rules_config(cfg_path)
    except Exception:
        _logger.exception(
            "RiskGuardFactory: 加载 / 校验 TOML 失败 (path=%s) — fail-open",
            cfg_path,
        )
        return None

    factory = RiskGuardFactory(
        cfg=cfg,
        pool=pool,  # type: ignore[arg-type]
        market_calendar=RoutingCalendar(),
    )
    _logger.info(
        "RiskGuardFactory ready: %d rules from %s (per-account LRU cache, "
        "calendar=RoutingCalendar[exchange_calendars 全市场])",
        factory.rule_count,
        cfg_path,
    )
    return factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动 / 停机钩子。

    - **DB pool**（D-8b）：持久化 orders / positions / trade_plans / accounts
    - **Backtest ProcessPool**（Swarm S1, ADR-0025）：CPU 重活子进程池 + 预热 + rlimit
    - **RiskGuardFactory**（D-9.1a, issue #8）：per-account RiskGuard cache，
      撮合前过风控拦截（每个 JWT user 派生独立 trade history 视图）
    """
    pool = await init_pool(_settings.database_url)
    init_backtest_pool(_settings)
    app.state.risk_guard_factory = await _build_risk_guard_factory(pool)

    # D-11 live runner：内存 task 随重启丢失，把残留 running 行 reconcile 成 errored
    # （避免 UNIQUE running 永久挡住该 candidate 重新 start）。
    async with get_conn() as conn:
        n = await runs_store.mark_running_as_errored(conn, reason="service restarted")
    if n:
        _logger.warning("live runner reconcile: %d 个残留 running run 标记为 errored", n)
    app.state.live_runner_manager = LiveRunnerManager(
        risk_guard_factory=app.state.risk_guard_factory,
        settings=_settings,
    )
    try:
        yield
    finally:
        await app.state.live_runner_manager.stop_all()
        shutdown_backtest_pool()
        await close_pool()


app = FastAPI(
    title="inalpha-paper",
    version=__version__,
    description="回测 / 模拟盘 / 实盘三合一引擎",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(backtest.router)
app.include_router(orders.router)
app.include_router(risk.router)
app.include_router(strategies.router)
app.include_router(strategy_candidates.router)
app.include_router(strategy_runs.router)
app.include_router(trade_plans.router)

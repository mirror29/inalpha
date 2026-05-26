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

from . import __version__
from .account_id import account_id_from_sub
from .api import (
    backtest,
    health,
    orders,
    risk,
    strategies,
    strategy_candidates,
    trade_plans,
)
from .config import get_paper_settings
from .engine.pool import init_pool as init_backtest_pool
from .engine.pool import shutdown_pool as shutdown_backtest_pool
from .execution.risk_guard import RiskGuard
from .execution.risk_rules import (
    PostgresTradeRepository,
    build_rules,
    load_risk_rules_config,
)
from .execution.risk_rules.base import ClosedTradeRecord, TradeRepository

_settings = get_paper_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)
_logger = logging.getLogger(__name__)


class _NoopTradeRepo:
    """空 TradeRepository 实现 —— D-9 范围内不接历史 trade 表。

    CooldownRule / StoplossGuardRule / LowProfitRule 用它查 ``get_closed_trades`` 返空 list
    → 它们的 ``check_*`` 永远不触发；MaxDrawdownRule 不依赖 trade_repo（看 starting_balance vs
    现金），仍可触发；MarketHoursRule 不依赖 trade_repo，但依赖 market_calendar。

    后续接 ``services/paper`` 的 closed_trades 表为单独 issue。
    """

    def get_closed_trades(self, **_: object) -> list[ClosedTradeRecord]:
        return []


def _resolve_config_path() -> Path | None:
    """从 settings 读 risk_rules.toml 路径，相对路径先工作目录后包根 fallback。

    sync 函数 —— 让 async ``_build_risk_guard`` 不直接做 Path IO（避开 ASYNC240）。
    """
    cfg_path = Path(_settings.risk_rules_config_path)
    if cfg_path.is_absolute():
        return cfg_path if cfg_path.exists() else None
    if cfg_path.exists():
        return cfg_path.resolve()
    pkg_root = Path(__file__).resolve().parent.parent.parent
    candidate = pkg_root / _settings.risk_rules_config_path
    return candidate if candidate.exists() else None


def _resolve_trade_repo(pool: object) -> TradeRepository:
    """选择 TradeRepository 实现。

    - env var ``INALPHA_RISK_DEMO_ACCOUNT_SUB`` 设置 → 用 ``PostgresTradeRepository`` 绑定到
      ``account_id_from_sub(sub)`` 派生的 UUID（demo / single-account 模式）
    - 否则 → ``_NoopTradeRepo``（默认 fail-open，4 条 trade-based rule 不触发）

    Demo 模式启用步骤：

        export INALPHA_RISK_DEMO_ACCOUNT_SUB=test-user      # 跟 JWT sub 对齐
        uv run python services/paper/scripts/demo_risk_seed.py --sub test-user
        # 重启 paper service → 用同 sub 跟 agent 对话验证 RISK_REJECTED
    """
    demo_sub = _settings.risk_demo_account_sub
    if not demo_sub:
        return _NoopTradeRepo()

    account_id = account_id_from_sub(demo_sub)
    repo = PostgresTradeRepository(account_id, pool, lookback_min=1440)  # type: ignore[arg-type]
    _logger.warning(
        "RiskGuard demo mode: PostgresTradeRepository bound to sub=%r account_id=%s "
        "(single-account; not production-ready, see issue #8)",
        demo_sub,
        account_id,
    )
    return repo


class _CryptoOnlyCalendar:
    """crypto 永远在交易时段 + 下次开盘=now。

    crypto 24/7 是 paper service 主用例（A 股 / 美股 market_hours 需求另开 issue）。
    """

    def is_trading_hours(self, *_: object, **__: object) -> bool:
        return True

    def next_session_open(self, _market: str, now):  # type: ignore[no-untyped-def]
        return now


async def _build_risk_guard(pool: object) -> RiskGuard | None:
    """读 TOML + build rules + 构造 RiskGuard。失败 → log error + 返 None（fail-open）。

    Demo 模式（``INALPHA_RISK_DEMO_ACCOUNT_SUB`` 设置）下额外 ``await repo.refresh()``
    一次把 closed_trades cache 拉到内存——seed 脚本注入后需要重启 paper service
    走这条路径让 cache 包含新 trade。
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
            "RiskGuard: risk_rules.toml 未找到 (path=%s) — fail-open，HTTP 下单不过风控",
            _settings.risk_rules_config_path,
        )
        return None

    try:
        cfg = load_risk_rules_config(cfg_path)
        trade_repo = _resolve_trade_repo(pool)
        rules = build_rules(
            cfg,
            trade_repo=trade_repo,
            market_calendar=_CryptoOnlyCalendar(),
        )
    except Exception:
        _logger.exception(
            "RiskGuard: 加载 / 构造规则失败 (path=%s) — fail-open，HTTP 下单不过风控",
            cfg_path,
        )
        return None

    # PostgresTradeRepository 用 async refresh + sync cache 模式 —— 启动时拉一次
    if isinstance(trade_repo, PostgresTradeRepository):
        try:
            n = await trade_repo.refresh()
            _logger.info(
                "PostgresTradeRepository refreshed: %d trades in cache (account=%s)",
                n,
                trade_repo._account_id,  # access private for log only
            )
        except Exception:
            _logger.exception(
                "PostgresTradeRepository.refresh() 失败 — cache 空，trade-based "
                "rule 暂时不会触发；可重启 paper service 重试"
            )

    guard = RiskGuard(rules=rules, starting_balance=cfg.starting_balance)
    _logger.info(
        "RiskGuard ready: loaded %d rules from %s — %s",
        guard.rule_count,
        cfg_path,
        guard.rule_names,
    )
    return guard


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动 / 停机钩子。

    - **DB pool**（D-8b）：持久化 orders / positions / trade_plans / accounts
    - **Backtest ProcessPool**（Swarm S1, ADR-0025）：CPU 重活子进程池 + 预热 + rlimit
    - **RiskGuard**（D-9, ADR-0006, issue #3）：HTTP 下单链路风控拦截
    """
    pool = await init_pool(_settings.database_url)
    init_backtest_pool(_settings)
    app.state.risk_guard = await _build_risk_guard(pool)
    try:
        yield
    finally:
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
app.include_router(trade_plans.router)

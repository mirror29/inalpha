"""``RiskGuardFactory`` —— per-account ``RiskGuard`` 实例工厂 + LRU cache。

D-9.1a 引入（issue #8）。在此之前 paper service 全局共享一个 ``RiskGuard`` 实例，
其内部 ``PostgresTradeRepository`` 绑定到 demo ``account_id`` —— 多用户场景下 A
用户平仓会让 B 用户的 cooldown 误触发。

设计：

- lifespan 时构造一次，持有 rules config + DB pool + market calendar
- 每次 ``await factory.get_for_check(account_id)`` 按 caller account_id 拿对应
  RiskGuard 实例；首次构造时按 account_id 建独立 PostgresTradeRepository
- ``OrderedDict`` LRU 缓存（默认 ``cache_size=64``），超出后逐出最旧的
- ``get_for_check`` 内部 ``await repo.refresh()`` 保证 trade-based rule 看到最新
  closed_trades（5s TTL 不够覆盖"平仓后立刻同 symbol 再下单"的 cooldown 场景）
- 并发安全：``asyncio.Lock`` 防同一 account 重复构造；refresh 在锁外（其内部
  自带 threading lock）

不在范围：
- 持久化 cache（重启 → 空 cache，靠 lazy 重建）
- ``risk_locks`` 表加 account_id 列（当前 lock 仍是 venue+symbol scoped 全局共享；
  多账户 lock 隔离独立 issue）
- ``starting_balance`` 仍从 config 读全局值（多账户独立余额留 D-10+）
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING
from uuid import UUID

from .risk_guard import RiskGuard
from .risk_rules import (
    PostgresTradeRepository,
    RiskRulesConfig,
    build_rules,
)
from .risk_rules.base import MarketCalendar

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class RiskGuardFactory:
    """Per-account RiskGuard 工厂 + LRU cache。

    Args:
        cfg: 已加载的 ``RiskRulesConfig``（lifespan 从 TOML 读一次共享）
        pool: ``inalpha_shared`` 注入的 async DB pool，给 ``PostgresTradeRepository``
        market_calendar: 共享 calendar 实例（按 venue 决定开闭市，多账户共享）
        cache_size: LRU 上限，默认 64
        lookback_min: ``PostgresTradeRepository`` 拉取窗口，默认 1440（24h）

    用法：
        factory = RiskGuardFactory(cfg=..., pool=..., market_calendar=...)
        guard = await factory.get_for_check(account_id)
        await guard.check(conn, instrument_id=..., side=..., now=...)
    """

    def __init__(
        self,
        *,
        cfg: RiskRulesConfig,
        pool: AsyncConnectionPool,
        market_calendar: MarketCalendar,
        cache_size: int = 64,
        lookback_min: int = 1440,
    ) -> None:
        if cache_size <= 0:
            raise ValueError(f"cache_size must be positive, got {cache_size}")
        if lookback_min <= 0:
            raise ValueError(f"lookback_min must be positive, got {lookback_min}")
        self._cfg = cfg
        self._pool = pool
        self._calendar = market_calendar
        self._cache_size = cache_size
        self._lookback_min = lookback_min
        self._cache: OrderedDict[UUID, tuple[PostgresTradeRepository, RiskGuard]] = (
            OrderedDict()
        )
        self._lock = asyncio.Lock()

    @property
    def cache_size_current(self) -> int:
        return len(self._cache)

    @property
    def rule_count(self) -> int:
        """共享配置的 rule 数（构造后不变）。"""
        return len(self._cfg.rules)

    @property
    def max_order_notional(self) -> float | None:
        """单笔名义价值硬上限（issue #42），``None`` = 不限制。供 ``check_order_notional`` 读。"""
        return self._cfg.max_order_notional

    async def get_for_check(self, account_id: UUID) -> RiskGuard:
        """获取 account 的 RiskGuard 并刷新其 trade cache。

        首次访问 → 构造 PostgresTradeRepository + RiskGuard，写入缓存（LRU 逐出最旧）；
        后续访问 → 命中缓存，move_to_end 保持 LRU 顺序。

        每次返回前 ``await repo.refresh()`` 一次。失败时仅 log warning，
        ``check`` 仍继续（用 stale cache，符合 fail-open）。
        """
        async with self._lock:
            if account_id in self._cache:
                self._cache.move_to_end(account_id)
                repo, guard = self._cache[account_id]
            else:
                repo = PostgresTradeRepository(
                    account_id,
                    self._pool,
                    lookback_min=self._lookback_min,
                )
                rules = build_rules(
                    self._cfg,
                    trade_repo=repo,
                    market_calendar=self._calendar,
                )
                guard = RiskGuard(
                    rules=rules,
                    starting_balance=self._cfg.starting_balance,
                )
                self._cache[account_id] = (repo, guard)
                if len(self._cache) > self._cache_size:
                    evicted_id, _ = self._cache.popitem(last=False)
                    logger.info(
                        "RiskGuardFactory: evicted account=%s (LRU, cache full %d)",
                        evicted_id,
                        self._cache_size,
                    )

        try:
            await repo.refresh()
        except Exception:
            logger.exception(
                "RiskGuardFactory: trade_repo refresh failed for account=%s — "
                "trade-based RiskRule will see stale cache (fail-open)",
                account_id,
            )

        return guard


__all__ = ["RiskGuardFactory"]

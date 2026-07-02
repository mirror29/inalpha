"""``PostgresTradeRepository`` —— sync 接口适配 RiskRule.TradeRepository Protocol（ADR-0007 §D4）。

桥接策略：

- ``RiskRule.check_*`` 同步调，需要 sync 接口 → 实现 ``get_closed_trades``
- DB 是 async psycopg → 用 **预加载 cache** 模式
- 外部周期性 ``await repo.refresh()`` 加载最近 ``lookback_min`` 窗口内 trades
- sync ``get_closed_trades`` 过滤 local cache（不进 DB）

TTL 兜底：调 sync 接口时若 cache 太老（> ``cache_ttl_seconds``）打 warning。
外部责任：挂同步任务保持 cache fresh（推荐挂在 ClosedTradesWriter flush 后 trigger）。
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from ...kernel.identifiers import InstrumentId
from .base import ClosedTradeRecord, Side

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class PostgresTradeRepository:
    """实现 ``RiskRule.TradeRepository`` Protocol（sync），内部 async DB + TTL cache。"""

    def __init__(
        self,
        account_id: UUID,
        db_pool: AsyncConnectionPool,
        *,
        lookback_min: int = 1440,
        cache_ttl_seconds: float = 5.0,
    ) -> None:
        if lookback_min <= 0:
            raise ValueError(f"lookback_min must be positive, got {lookback_min}")
        if cache_ttl_seconds <= 0:
            raise ValueError(f"cache_ttl_seconds must be positive, got {cache_ttl_seconds}")
        self._account_id = account_id
        self._db_pool = db_pool
        self._lookback_min = lookback_min
        self._cache_ttl = cache_ttl_seconds
        self._cache: list[ClosedTradeRecord] = []
        self._cache_loaded_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def cache_loaded_at(self) -> datetime | None:
        return self._cache_loaded_at

    async def refresh(self, now: datetime | None = None) -> int:
        """从 DB 拉最近 ``lookback_min`` 分钟内 closed_trades，覆盖 cache。返回行数。

        调用方应周期性调（如 ClosedTradesWriter flush 后 trigger）。

        **reset epoch 收口**:窗口起点不早于账户最近一次 reset——重置 = 绩效新纪元,
        否则 lookback 窗口内的旧亏损会在重置后的"干净"账户重新触发
        MaxDrawdown/LowProfit 锁(旧成交属上一轮口径,不该再参与风控判定)。
        """
        from ...storage import accounts as accounts_store
        from ...storage import closed_trades as trades_store

        if now is None:
            now = datetime.now(UTC)
        close_after = now - timedelta(minutes=self._lookback_min)

        async with self._db_pool.connection() as conn:
            last_reset = await accounts_store.last_reset_at(conn, self._account_id)
            if last_reset is not None and last_reset > close_after:
                close_after = last_reset
            rows = await trades_store.list_recent(
                conn,
                account_id=self._account_id,
                close_after=close_after,
                limit=10_000,
            )

        records = [
            ClosedTradeRecord(
                instrument_id=InstrumentId(symbol=r["symbol"], venue=r["venue"]),
                side=r["side"],
                open_ts=r["open_ts"],
                close_ts=r["close_ts"],
                close_profit_pct=float(r["close_profit_pct"]),
                close_profit_abs=float(r["close_profit_abs"]),
                exit_reason=r["exit_reason"],
            )
            for r in rows
        ]

        with self._lock:
            self._cache = records
            self._cache_loaded_at = now
        return len(records)

    def get_closed_trades(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        close_after: datetime,
        close_before: datetime | None = None,
        side: Side | None = None,
        exit_reasons: list[str] | None = None,
        max_profit_pct: float | None = None,
    ) -> list[ClosedTradeRecord]:
        """同步过滤 local cache。Cache stale 时打 warning 仍返回（不阻塞 RiskEngine sync 路径）。"""
        self._warn_if_stale()

        with self._lock:
            out = [t for t in self._cache if t.close_ts >= close_after]

        if close_before is not None:
            out = [t for t in out if t.close_ts < close_before]
        if instrument_id is not None:
            out = [t for t in out if t.instrument_id == instrument_id]
        if side is not None and side != "*":
            out = [t for t in out if t.side == side]
        if exit_reasons is not None:
            out = [t for t in out if t.exit_reason in exit_reasons]
        if max_profit_pct is not None:
            out = [t for t in out if t.close_profit_pct < max_profit_pct]
        return out

    def _warn_if_stale(self) -> None:
        if self._cache_loaded_at is None:
            logger.warning(
                "PostgresTradeRepository(account=%s): cache never refreshed; "
                "trade-based RiskRule will see empty list",
                self._account_id,
            )
            return
        age = (datetime.now(UTC) - self._cache_loaded_at).total_seconds()
        if age > self._cache_ttl:
            logger.warning(
                "PostgresTradeRepository(account=%s): cache stale (age=%.1fs > ttl=%.1fs); "
                "trade-based RiskRule may see outdated trades",
                self._account_id, age, self._cache_ttl,
            )


__all__ = ["PostgresTradeRepository"]

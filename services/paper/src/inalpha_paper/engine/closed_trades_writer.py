"""``ClosedTradesWriter`` —— Portfolio close 队列 → PostgreSQL closed_trades 异步写入。

[ADR-0007 §D3](../../../../docs/miro/decisions/0007-closed-trades-write-path.md) 设计的
sync→async 桥接。Portfolio sync callback 把检测到的 close 入内存队列；本 worker
async 周期性 drain 队列写 DB（参考 [`risk_rules/reconciler.py`](../execution/risk_rules/reconciler.py)
模式）。

工作模式：

- ``flush_once()``：单次 drain Portfolio 队列 + batch INSERT closed_trades。
  失败时 staging 已被 drain 到本地变量，**重试机制把没写成功的 staging 重入队**
  （Portfolio.append 不暴露，本 worker 内部 buffer）。
- ``run_forever(stop_event)``：后台 task 主循环，每 ``interval_seconds`` 跑一次。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .close_detector import ClosedTradeStaging

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from .portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WriteStats:
    inserted: int = 0
    retry_buffered: int = 0
    """本次 flush 失败、回到内部重试 buffer 的条数。"""


class ClosedTradesWriter:
    """周期性把 Portfolio close 队列同步到 closed_trades 表。"""

    def __init__(
        self,
        portfolio: Portfolio,
        db_pool: AsyncConnectionPool,
        *,
        interval_seconds: float = 2.0,
        batch_size: int = 50,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be positive, got {interval_seconds}")
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        self._portfolio = portfolio
        self._db_pool = db_pool
        self._interval = interval_seconds
        self._batch_size = batch_size
        # 写失败回到此 buffer，下次 flush 优先尝试
        self._retry_buffer: list[ClosedTradeStaging] = []

    async def flush_once(self) -> WriteStats:
        """单次 drain + 写 DB。幂等：队列空 + buffer 空时返 (0, 0)。"""
        from ..storage import closed_trades as trades_store

        # 1. 优先 retry buffer + drain Portfolio 新数据
        pending = list(self._retry_buffer)
        self._retry_buffer.clear()
        pending.extend(self._portfolio.drain_closed_trades())

        if not pending:
            return WriteStats(inserted=0, retry_buffered=0)

        inserted = 0
        retry: list[ClosedTradeStaging] = []

        # 2. batch 写。失败时单条计入 retry buffer（不阻断 batch 其余）
        for batch in _chunk(pending, self._batch_size):
            try:
                async with self._db_pool.connection() as conn:
                    for staging in batch:
                        try:
                            await trades_store.insert_close(
                                conn,
                                account_id=staging.account_id,
                                venue=staging.venue,
                                symbol=staging.symbol,
                                side=staging.side,
                                open_ts=staging.open_ts,
                                close_ts=staging.close_ts,
                                open_price=staging.open_price,
                                close_price=staging.close_price,
                                quantity=staging.quantity,
                                close_profit_pct=staging.close_profit_pct,
                                close_profit_abs=staging.close_profit_abs,
                                exit_reason=staging.exit_reason,
                                open_order_id=staging.open_order_id,
                                close_order_id=staging.close_order_id,
                            )
                            inserted += 1
                        except Exception:
                            logger.exception(
                                "ClosedTradesWriter: insert failed for %s/%s (%s); buffer for retry",
                                staging.venue, staging.symbol, staging.exit_reason,
                            )
                            retry.append(staging)
                    await conn.commit()
            except Exception:
                logger.exception(
                    "ClosedTradesWriter: batch commit failed; %d entries buffered for retry",
                    len(batch),
                )
                retry.extend(batch)

        self._retry_buffer = retry
        return WriteStats(inserted=inserted, retry_buffered=len(retry))

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """后台主循环。``stop_event.set()`` 后 graceful 退出。"""
        logger.info(
            "ClosedTradesWriter started (interval=%.1fs, batch=%d)",
            self._interval, self._batch_size,
        )
        try:
            while not stop_event.is_set():
                try:
                    stats = await self.flush_once()
                    if stats.inserted > 0 or stats.retry_buffered > 0:
                        logger.info(
                            "flush: inserted=%d retry_buffered=%d",
                            stats.inserted, stats.retry_buffered,
                        )
                except Exception:
                    logger.exception("flush_once unexpected failure; will retry next tick")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                except TimeoutError:
                    pass
        finally:
            logger.info("ClosedTradesWriter stopped")


def _chunk(items: list[ClosedTradeStaging], size: int) -> list[list[ClosedTradeStaging]]:
    return [items[i : i + size] for i in range(0, len(items), size)]

"""ClosedTrades 写入路径 + 读取 cache 的 lifecycle helper（ADR-0007 Slice 6）。

提供 turn-key 接入：

```python
# lifespan startup
manager = ClosedTradesPipeline.start(portfolio, db_pool, account_id)

# RiskEngine 用 manager.trade_repo（实现 TradeRepository Protocol）
risk_engine = RiskEngine(bus, rules=rules, ..., )  # trade_repo 通过 rules 注入

# lifespan shutdown
await manager.stop()
```

包含 3 个协作部件：

- `Portfolio` 已有 close 队列（ADR-0007 Slice 3）
- `ClosedTradesWriter` async worker（Slice 4）周期 drain 写 DB
- `PostgresTradeRepository` sync 接口（Slice 5）+ 同步 refresh 跟 writer 步调一致

Writer flush 完成后自动触发 repo.refresh，让 RiskRule 看到最新 trade 不超过
一个 worker tick（默认 2 秒）的延迟。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from ..execution.risk_rules.postgres_repo import PostgresTradeRepository
from .closed_trades_writer import ClosedTradesWriter

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from .portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClosedTradesPipeline:
    """closed_trades 写入 + 读取 lifecycle 包。"""

    portfolio: Portfolio
    writer: ClosedTradesWriter
    trade_repo: PostgresTradeRepository
    _stop_event: asyncio.Event
    _task: asyncio.Task[None] | None

    @classmethod
    async def start(
        cls,
        portfolio: Portfolio,
        db_pool: AsyncConnectionPool,
        account_id: UUID,
        *,
        writer_interval: float = 2.0,
        repo_lookback_min: int = 1440,
        repo_cache_ttl: float = 5.0,
    ) -> ClosedTradesPipeline:
        """构造 writer + repo + asyncio task。`stop()` 优雅退出。

        **立刻 await initial refresh 一次**，保证调用方拿到 pipeline 时 cache 已就绪
        （避免"测试 / 启动后第一笔 RiskRule check 拿到空 cache"的 race）。

        Args:
            portfolio: 已挂 account_id 的 Portfolio（drain_closed_trades 才有数据）
            db_pool: PostgreSQL async pool
            account_id: 必须与 portfolio.account_id 一致
            writer_interval: writer flush 周期（秒）
            repo_lookback_min: PostgresTradeRepository 预加载窗口（默认 24h，覆盖大部分 RiskRule.lookback_min）
            repo_cache_ttl: stale warning 阈值（秒）
        """
        writer = ClosedTradesWriter(portfolio, db_pool, interval_seconds=writer_interval)
        repo = PostgresTradeRepository(
            account_id, db_pool,
            lookback_min=repo_lookback_min,
            cache_ttl_seconds=repo_cache_ttl,
        )
        # initial flush + refresh：确保 pipeline 返回时 cache 已就绪
        await writer.flush_once()
        await repo.refresh()
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            _run_pipeline(writer, repo, stop_event, writer_interval)
        )
        logger.info(
            "ClosedTradesPipeline started (writer_interval=%.1fs, lookback=%dmin)",
            writer_interval, repo_lookback_min,
        )
        return cls(
            portfolio=portfolio,
            writer=writer,
            trade_repo=repo,
            _stop_event=stop_event,
            _task=task,
        )

    async def stop(self) -> None:
        """优雅停止。等 task 退出 + 最后 flush 一次确保数据落盘。"""
        self._stop_event.set()
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=10.0)
            self._task = None
        await self.writer.flush_once()
        logger.info("ClosedTradesPipeline stopped + final flush completed")


async def _run_pipeline(
    writer: ClosedTradesWriter,
    repo: PostgresTradeRepository,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    """worker 主循环：每个 tick **先 flush 写 DB，再 refresh repo cache**。

    顺序很关键：先写后读保证 repo 看到本 tick Portfolio 产生的新 close。
    """
    while not stop_event.is_set():
        try:
            await writer.flush_once()
            await repo.refresh()
        except Exception:
            logger.exception("ClosedTradesPipeline tick failed; will retry next tick")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass

"""指数成分每日快照调度 —— ADR-0053 阶段 C 向前累积的运营臂。

akshare 只回**当前**成分，免费历史成分拿不到 → 唯一 PIT 路径是从启用日起每日把
"当前成分"以 ``as_of_date=今天`` 落 ``constituent_snapshot``。本模块在 data 服务
lifespan 里起一个后台循环，按 ``CONSTITUENT_SNAPSHOT_INDICES`` 周期性补当天快照。

设计要点：

- **幂等**：每轮先查"今天是否已有快照"，有则跳过——省 akshare 调用 + 防封，且重启
  频繁（uvicorn --reload）也不会重复打源站。
- **catch-up**：启动即跑一轮，补上停机期间缺的当天快照（不回填历史，accumulate forward）。
- **per-index 隔离**：单个指数拉取失败只 warning 跳过，不拖垮整轮/其他指数。
- 手动 ``POST /constituents/snapshot`` 与本调度共用 :func:`record_snapshot`，行为一致。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from inalpha_shared import get_logger
from inalpha_shared.db import get_conn
from inalpha_shared.errors import InalphaError
from psycopg import AsyncConnection

from .connectors.akshare import get_connector as get_akshare_connector
from .storage import constituents as store

_logger = get_logger(__name__)


class ConstituentsUnavailableError(InalphaError):
    """源站拉取失败 / 不支持该指数 —— 不静默写空（§3.1）。"""

    code = "CONSTITUENTS_UNAVAILABLE"
    status_code = 502


async def record_snapshot(
    db: AsyncConnection, *, index_code: str, as_of_date: date | None = None
) -> tuple[str, int]:
    """拉 ``index_code`` 当前成分（akshare）落库 ``as_of_date``，返回 ``(日期, 条数)``。

    akshare 只回当前成分，故这是 PIT 史的**唯一来源**；源站失败 → 抛
    :class:`ConstituentsUnavailableError`，不静默写空（§3.1）。调用方负责开事务。

    ``as_of_date`` 由调用方传入则用之（调度器跨午夜场景：``_tick`` 在入口锁定当天，避免
    akshare HTTP 跨过午夜后这里重算成 D+1、让 D 永久空洞）；省略=用"现在"（HTTP 端点）。
    """
    try:
        conn = get_akshare_connector()
    except RuntimeError as exc:  # akshare connector 未注册（启动未 init）
        raise ConstituentsUnavailableError(
            f"akshare connector unavailable: {exc}", code="CONSTITUENTS_UNAVAILABLE"
        ) from exc

    members = await conn.fetch_index_constituents(index_code)
    if not members:
        raise ConstituentsUnavailableError(
            f"no constituents fetched for index {index_code!r} "
            "(akshare 拉取失败 / 不支持该指数)",
            details={"index_code": index_code},
        )

    snap_day = as_of_date or datetime.now(UTC).date()
    async with db.transaction():
        n = await store.upsert_snapshot(
            db, index_code=index_code, as_of_date=snap_day, constituents=members
        )
    _logger.info("constituent_snapshot_recorded", index_code=index_code, count=n)
    return snap_day.isoformat(), n


def parse_indices(raw: str) -> list[str]:
    """解析逗号分隔的指数代码配置，去空白/去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


class ConstituentSnapshotScheduler:
    """成分快照后台调度器：周期性为追踪的指数补当天 PIT 快照（幂等）。"""

    def __init__(self, *, index_codes: list[str], interval_s: float) -> None:
        self._index_codes = index_codes
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """起后台循环；无追踪指数则不启动（调度禁用）。"""
        if not self._index_codes:
            _logger.info("constituent_scheduler_disabled", reason="no tracked indices")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="constituent-snapshot-scheduler"
        )
        _logger.info(
            "constituent_scheduler_started",
            indices=self._index_codes,
            interval_s=self._interval_s,
        )

    async def stop(self) -> None:
        """取消后台循环并等其收尾。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 整轮兜底：调度不能因单轮异常死掉
                _logger.warning("constituent_scheduler_tick_failed", error=str(exc))
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise

    async def _tick(self) -> None:
        """为每个追踪指数补当天快照——今天已有则跳过（幂等）。"""
        today = datetime.now(UTC).date()
        for index_code in self._index_codes:
            try:
                async with get_conn() as db:
                    snap_date, _ = await store.get_constituents(
                        db, index_code=index_code, as_of=today
                    )
                    if snap_date == today:
                        continue  # 今天已有快照，省一次源站调用
                    # 传 as_of_date=today：锁定入口当天，akshare HTTP 跨午夜也不写成 D+1
                    await record_snapshot(db, index_code=index_code, as_of_date=today)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单指数失败隔离，不拖累其他指数/整轮
                _logger.warning(
                    "constituent_snapshot_skip",
                    index_code=index_code,
                    error=str(exc),
                )

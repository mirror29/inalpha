"""Connector Protocol + venue → connector 注册表。

设计：

- 各 venue 的 connector（binance/alpaca/akshare/…）各自维护 module-level singleton，
  内部状态由自己的 ``init_connector`` / ``close_connector`` 管，不共享一份生命周期
- 本模块只提供"按 venue 取已注册 connector"的查表入口，给 ``api/backfill.py`` 用
- 单独抽 Protocol 让 mypy 帮我们在 backfill 路由时对齐 ``fetch_bars`` 签名

每个 connector 的 ``fetch_bars`` **必须返回统一格式**：
``list[tuple[datetime, open, high, low, close, volume]]``，``datetime`` 是 UTC aware。
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


class Connector(Protocol):
    """所有 venue connector 的最小契约。"""

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """拉 ``[since, since + limit*timeframe]`` 区间的 OHLCV。

        Returns:
            list of ``(ts, open, high, low, close, volume)``，``ts`` UTC aware。
            空列表表示该窗口无数据。
        """
        ...

    async def close(self) -> None:
        """关闭底层 SDK / HTTP 客户端。"""
        ...


@runtime_checkable
class TickerCapable(Protocol):
    """可选能力：实时 ticker 拉取（``GET /ticker?fresh=true`` 路径）。

    并非所有 venue 都有"实时单价"语义（如 FRED 是发布周期性宏观数据；akshare A 股
    实时 spot 拉全表性能差），所以这是 ``Connector`` 之外的**可选**鸭子接口。
    ``ticker.py`` 用 ``isinstance(c, TickerCapable)`` 判断；未实现的 venue 在
    fresh=true 路径返 ``FRESH_NOT_SUPPORTED_FOR_VENUE``，建议 caller 走 fresh=false。
    """

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        """单次实时拉 ``symbol`` 最新成交价。

        Returns:
            ``(ts, last_price)``，``ts`` UTC aware；``ts`` 是交易所/数据源给的报价时间，
            不是本地 ``datetime.now()``。
        """
        ...


# ─── 注册表 ──────────────────────────────────────────────────────────

_REGISTRY: dict[str, Connector] = {}


def register_connector(venue: str, connector: Connector) -> None:
    """connector 自己的 ``init_connector`` 里调用，登记到注册表。

    重复登记同一 venue 会抛 —— 让 startup 双调的 bug 早暴露。
    """
    if venue in _REGISTRY:
        raise RuntimeError(f"connector for venue {venue!r} already registered")
    _REGISTRY[venue] = connector


def unregister_connector(venue: str) -> None:
    """``close_connector`` 里调用，从注册表移除。幂等。"""
    _REGISTRY.pop(venue, None)


def get_connector_for_venue(venue: str) -> Connector:
    """FastAPI dependency / api 路由用 —— 没注册的 venue 抛 ``KeyError``。"""
    try:
        return _REGISTRY[venue]
    except KeyError as e:
        raise KeyError(
            f"no connector registered for venue {venue!r}; "
            f"known venues: {sorted(_REGISTRY.keys())}"
        ) from e


def list_registered_venues() -> list[str]:
    """注册的 venue 列表 —— health endpoint / 错误信息用。"""
    return sorted(_REGISTRY.keys())

"""Binance CCXT async 包装 —— 模块级 singleton，跟 db pool 同样模式。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt
from quant_lab_shared import get_logger

_logger = get_logger(__name__)

# CCXT timeframe 到秒数的映射 —— backfill 推 cursor 时用
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}


class BinanceConnector:
    """轻封装 ccxt.async_support.binance。

    生命周期：``init_connector`` 启动时建一次，``close_connector`` 关停时清。
    """

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._exchange: Any = ccxt.binance(
            {
                "apiKey": api_key or None,
                "secret": api_secret or None,
                "enableRateLimit": True,
                # 只加载现货 markets：CCXT 默认会同时拉 spot + fapi（USDM 期货）+
                # dapi（COINM 期货）+ options 的 exchangeInfo，部分网络/VPN 出口对
                # fapi.binance.com 不通会整个 loadMarkets 失败。MVP 只用现货。
                "options": {
                    "defaultType": "spot",
                    "fetchMarkets": ["spot"],
                },
            }
        )

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 Binance 拉 OHLCV。

        Returns:
            list of (ts, open, high, low, close, volume)；ts 是 UTC aware datetime。
        """
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"unsupported timeframe: {timeframe}")

        since_ms = int(since.timestamp() * 1000)
        _logger.debug(
            "binance_fetch_ohlcv",
            symbol=symbol,
            timeframe=timeframe,
            since=since.isoformat(),
            limit=limit,
        )
        raw = await self._exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        return [
            (
                datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                float(o),
                float(h),
                float(low),
                float(c),
                float(v),
            )
            for ts_ms, o, h, low, c, v in raw
        ]

    async def close(self) -> None:
        await self._exchange.close()


# ---------- module-level singleton（跟 quant_lab_shared.db 同模式） ----------

_connector: BinanceConnector | None = None


def init_connector(api_key: str = "", api_secret: str = "") -> BinanceConnector:
    """启动时调一次。多次调会抛错。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("Binance connector already initialized")
    _connector = BinanceConnector(api_key=api_key, api_secret=api_secret)
    return _connector


async def close_connector() -> None:
    """关停时调一次。"""
    global _connector
    if _connector is None:
        return
    await _connector.close()
    _connector = None


def get_connector() -> BinanceConnector:
    """FastAPI dependency。"""
    if _connector is None:
        raise RuntimeError("Binance connector not initialized; call init_connector() first")
    return _connector

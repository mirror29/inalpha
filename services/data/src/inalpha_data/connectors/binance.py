"""Binance CCXT async 包装 —— 模块级 singleton，跟 db pool 同样模式。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt
from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "binance"

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
        self._api_key = api_key
        self._api_secret = api_secret
        # USDT-M 永续(linear)用独立 ccxt 实例懒加载:与现货分开,避免拉 fapi markets 失败
        # 时连累现货 loadMarkets(部分网络对 fapi.binance.com 不通)。仅在请求 perp 数据时建。
        self._futures_exchange: Any = None

    def _futures(self) -> Any:
        """懒建 USDT-M 永续(linear)ccxt 实例。"""
        if self._futures_exchange is None:
            self._futures_exchange = ccxt.binance(
                {
                    "apiKey": self._api_key or None,
                    "secret": self._api_secret or None,
                    "enableRateLimit": True,
                    "options": {"defaultType": "future"},
                }
            )
        return self._futures_exchange

    async def fetch_perp_funding_rate(self, symbol: str) -> dict[str, Any]:
        """拉 USDT-M 永续的 **mark price + 当期 funding rate**（ccxt ``fetch_funding_rate``）。

        ``symbol`` 用 ccxt 永续记法 ``BTC/USDT:USDT``。``fapi.binance.com`` 不通时 ccxt 抛
        ``NetworkError`` / ``ExchangeError`` —— 让上层翻 5xx 或 fallback（funding=0 + 标注失真）。

        Returns:
            ``{symbol, mark_price, funding_rate, ts, next_funding_ts}``。
        """
        ex = self._futures()
        fr = await ex.fetch_funding_rate(symbol)
        mark = fr.get("markPrice")
        rate = fr.get("fundingRate")
        if mark is None or rate is None:
            raise ValueError(
                f"binance funding rate for {symbol} missing markPrice/fundingRate"
            )
        ts_ms = fr.get("timestamp")
        next_ms = fr.get("fundingTimestamp")
        return {
            "symbol": symbol,
            "mark_price": float(mark),
            "funding_rate": float(rate),
            "ts": (
                datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
                if ts_ms is not None
                else datetime.now(UTC)
            ),
            "next_funding_ts": (
                datetime.fromtimestamp(int(next_ms) / 1000, tz=UTC)
                if next_ms is not None
                else None
            ),
        }

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 Binance 拉 OHLCV。

        永续合约符号（如 ``BTC/USDT:USDT``）的 OHLCV **用现货同对作价格 proxy**：
        永续标记价≈现货价，K 线走势一致；剥掉 ``:USDT`` 后缀走现货 OHLCV 端点
        （现货 ccxt markets 里没有带 ``:`` 的合约符号，直接拉会失败）。资金费 / mark
        另走 ``fetch_perp_funding_rate`` / ``/perp/funding``，不在此 proxy。

        Returns:
            list of (ts, open, high, low, close, volume)；ts 是 UTC aware datetime。
        """
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"unsupported timeframe: {timeframe}")

        # 永续符号剥成现货同对作价格 proxy（BTC/USDT:USDT → BTC/USDT）
        fetch_symbol = symbol.split(":", 1)[0] if ":" in symbol else symbol

        since_ms = int(since.timestamp() * 1000)
        _logger.debug(
            "binance_fetch_ohlcv",
            symbol=symbol,
            fetch_symbol=fetch_symbol,
            timeframe=timeframe,
            since=since.isoformat(),
            limit=limit,
        )
        raw = await self._exchange.fetch_ohlcv(
            fetch_symbol, timeframe, since=since_ms, limit=limit
        )
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

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        """实时拉 ``symbol`` 的最新成交价（CCXT fetch_ticker）。

        Returns:
            ``(ts, last_price)``，``ts`` 是 UTC aware（CCXT 给的 timestamp ms）。

        Raises:
            ccxt.NetworkError / ccxt.ExchangeError —— 让上层翻成 5xx 或选择 fallback。

        何时用：``/ticker?fresh=true`` 路径；普通 backfill 仍走 ``fetch_bars``。

        坑：fetch_ticker 不走 rate-limit 优化的 batch 端点，**不要**在循环里高频调；
            单次 ~200-500ms 网络抖动可预期。
        """
        raw = await self._exchange.fetch_ticker(symbol)
        ts_ms = raw.get("timestamp")
        last = raw.get("last") or raw.get("close")
        if last is None:
            raise ValueError(f"binance ticker for {symbol} has no last/close price")
        if ts_ms is None:
            # now() 兜底会让上层 is_stale 恒 false（issue #62）；crypto 24/7 风险低但留痕，
            # 真发生说明 ccxt/交易所行为变了，值得被看见
            _logger.warning("binance ticker %s: ccxt 未给 timestamp，用本地 now() 兜底", symbol)
        ts = (
            datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
            if ts_ms is not None
            else datetime.now(UTC)
        )
        return ts, float(last)

    async def close(self) -> None:
        await self._exchange.close()
        if self._futures_exchange is not None:
            await self._futures_exchange.close()


# ---------- module-level singleton（跟 inalpha_shared.db 同模式） ----------

_connector: BinanceConnector | None = None


def init_connector(api_key: str = "", api_secret: str = "") -> BinanceConnector:
    """启动时调一次。多次调会抛错。同时登记到 ``_base`` 注册表。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("Binance connector already initialized")
    _connector = BinanceConnector(api_key=api_key, api_secret=api_secret)
    register_connector(VENUE, _connector)
    return _connector


async def close_connector() -> None:
    """关停时调一次。同步从注册表移除。"""
    global _connector
    if _connector is None:
        return
    await _connector.close()
    unregister_connector(VENUE)
    _connector = None


def get_connector() -> BinanceConnector:
    """FastAPI dependency。"""
    if _connector is None:
        raise RuntimeError("Binance connector not initialized; call init_connector() first")
    return _connector

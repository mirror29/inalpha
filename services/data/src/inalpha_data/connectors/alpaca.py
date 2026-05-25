"""Alpaca 美股 OHLCV connector —— 走 alpaca-py SDK + IEX free feed。

为什么选 Alpaca free 而不是 yfinance：

- yfinance 是非官方 wrapper，2024 起 Yahoo 频繁 429，生产不可靠
- Alpaca 免费档（注册即拿 key，无信用卡）给 IEX feed：200 rpm + 7y+ 历史 + WS 实时
- 用 ``StockHistoricalDataClient`` 走 REST，本 connector 只接 bars（与 binance/akshare 一致）

实现注：

- IEX feed 实时数据 15 分钟延迟（free 档限制）；历史 OHLCV 完整
- ``timeframe`` 映射到 ``alpaca.data.timeframe.TimeFrame``：1m / 5m / 15m / 1h / 1d
- 全 venue 共用注册表 ``_base``，由 ``main.lifespan`` 在 startup 调 ``init_connector``
- API key 缺失时不抛——直接走 ``raw_data=False`` 模式，IEX free 数据本身就免 key 也能拉，
  但有 key 会拿到更稳定的限速 + 错误信息（alpaca-py 自己处理无 key 退化）
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "alpaca"

# Alpaca SDK 的 TimeFrame 字符串映射 —— 跟 binance 的秒数映射对齐
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}


def _to_alpaca_timeframe(tf: str):  # type: ignore[no-untyped-def]
    """Inalpha 内部 timeframe 字符串 → ``alpaca.data.timeframe.TimeFrame``。

    延迟 import 让 alpaca-py 不安装时本模块仍能 import（测试可 mock）。
    """
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    if tf == "1m":
        return TimeFrame(1, TimeFrameUnit.Minute)
    if tf == "5m":
        return TimeFrame(5, TimeFrameUnit.Minute)
    if tf == "15m":
        return TimeFrame(15, TimeFrameUnit.Minute)
    if tf == "1h":
        return TimeFrame(1, TimeFrameUnit.Hour)
    if tf == "1d":
        return TimeFrame(1, TimeFrameUnit.Day)
    raise ValueError(f"alpaca: unsupported timeframe {tf!r}")


class AlpacaConnector:
    """alpaca-py ``StockHistoricalDataClient`` 包装。"""

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        from alpaca.data.historical import StockHistoricalDataClient

        # alpaca-py 接受空 key（部分公开数据可拉），但有 key 限速更友好
        self._client = StockHistoricalDataClient(
            api_key=api_key or None,
            secret_key=api_secret or None,
            raw_data=False,
        )

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 Alpaca 拉 OHLCV。

        Args:
            symbol: 美股代号，如 ``"AAPL"`` / ``"MSFT"``；不需要 venue 前缀
            timeframe: 见 ``TIMEFRAME_SECONDS`` 支持的 5 档
            since: UTC datetime；alpaca-py 自己处理 ISO 转换
            limit: 单次最多拉多少根（alpaca 默认 10000）

        Returns:
            list of ``(ts, open, high, low, close, volume)``，UTC aware。
        """
        from alpaca.data.requests import StockBarsRequest

        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"alpaca: unsupported timeframe {timeframe!r}")

        # alpaca SDK 是同步的；用 asyncio.to_thread 跑到线程池避免阻塞 event loop
        import asyncio

        def _fetch_sync() -> list[tuple[datetime, float, float, float, float, float]]:
            # 估算 end_ts：since + limit * timeframe（不超过 now）
            tf_secs = TIMEFRAME_SECONDS[timeframe]
            end_estimate = since + timedelta(seconds=tf_secs * limit)
            now = datetime.now(UTC)
            end = min(end_estimate, now)

            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=_to_alpaca_timeframe(timeframe),
                start=since,
                end=end,
                limit=limit,
            )
            _logger.debug(
                "alpaca_fetch_bars",
                symbol=symbol,
                timeframe=timeframe,
                since=since.isoformat(),
                end=end.isoformat(),
                limit=limit,
            )
            resp = self._client.get_stock_bars(req)
            # resp.data 是 {symbol: [Bar, ...]}（multi-symbol 时），
            # 单 symbol 时 resp.data[symbol] 是 list[Bar]
            bars_list = resp.data.get(symbol, []) if hasattr(resp, "data") else []
            out: list[tuple[datetime, float, float, float, float, float]] = []
            for b in bars_list:
                ts = b.timestamp
                # alpaca 给的 ts 可能不是 UTC aware —— 防御性补 tz
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                out.append(
                    (
                        ts,
                        float(b.open),
                        float(b.high),
                        float(b.low),
                        float(b.close),
                        float(b.volume),
                    )
                )
            return out

        return await asyncio.to_thread(_fetch_sync)

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        """实时拉 ``symbol`` 最新成交价（``get_stock_latest_trade``）。

        Returns:
            ``(ts, last_price)``，``ts`` UTC aware（alpaca trade.timestamp）。

        Raises:
            ValueError: 无最新成交（停牌 / 罕见 ticker）。

        坑：alpaca IEX free feed **仅 IEX 一个交易所的报价**，非全国合并 NBBO；
            盘前盘后可能 stale 几分钟。下单前如需更准 ref，需付费 SIP feed。
        """
        import asyncio

        from alpaca.data.requests import StockLatestTradeRequest

        def _fetch_sync() -> tuple[datetime, float]:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            resp = self._client.get_stock_latest_trade(req)
            # resp 是 {symbol: Trade}
            trade = resp.get(symbol) if hasattr(resp, "get") else None
            if trade is None:
                raise ValueError(f"alpaca: no latest trade for {symbol!r}")
            ts = trade.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return ts, float(trade.price)

        return await asyncio.to_thread(_fetch_sync)

    async def close(self) -> None:
        # alpaca-py StockHistoricalDataClient 没有 close 方法（用 requests session 自管理）
        return None


# ---------- module-level singleton ----------

_connector: AlpacaConnector | None = None


def init_connector(api_key: str = "", api_secret: str = "") -> AlpacaConnector | None:
    """启动时调一次。

    **key 缺失时返 ``None`` 不 register**——让 services/data 在没配置 alpaca key 的
    dev 环境下也能起；alpaca venue 自然不可达，请求会得到清晰的"venue not registered"
    错误。alpaca-py SDK 0.30+ 强制要求 api_key 非空，无 key 不能延迟初始化。
    """
    global _connector
    if _connector is not None:
        raise RuntimeError("Alpaca connector already initialized")
    if not api_key or not api_secret:
        _logger.info(
            "alpaca_connector_skipped",
            reason="ALPACA_API_KEY / ALPACA_API_SECRET not set",
        )
        return None
    _connector = AlpacaConnector(api_key=api_key, api_secret=api_secret)
    register_connector(VENUE, _connector)
    return _connector


async def close_connector() -> None:
    """关停时调一次。从注册表移除。幂等（key 缺失从未 register 时也安全）。"""
    global _connector
    if _connector is None:
        return
    await _connector.close()
    unregister_connector(VENUE)
    _connector = None


def get_connector() -> AlpacaConnector:
    if _connector is None:
        raise RuntimeError("Alpaca connector not initialized; call init_connector() first")
    return _connector

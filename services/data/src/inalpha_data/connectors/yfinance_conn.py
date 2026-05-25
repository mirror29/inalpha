"""yfinance 全球兜底 connector —— 覆盖 akshare/alpaca 没覆盖的市场。

为什么用 yfinance：

- **覆盖广**：Yahoo Finance 上的所有 ticker 都能拉——韩股 / 澳股 / 印股 / 巴西 / 加拿大 /
  全球指数 / 全球 ETF；akshare 没标准接口的市场都靠它兜底
- **零 key**：不需要注册任何账号
- **历史深**：日级几十年；分钟级近 60 天
- **缺点**：Yahoo 2024 起反爬偏严，偶发 429；prod 慎用，dev / 研究够用

**symbol 格式约定**（venue=``"yfinance"``）：直接用 Yahoo 原 ticker。

| 市场 | 示例 ticker | 说明 |
|---|---|---|
| 日经指数 | ``^N225`` | Yahoo 指数前缀 ``^`` |
| 日股单股 | ``6758.T`` | 东证后缀 ``.T`` |
| 韩国 KOSPI | ``^KS11`` | 指数 |
| 韩股单股 | ``005930.KS`` | 后缀 ``.KS`` |
| 澳大利亚 ASX 200 | ``^AXJO`` | 指数 |
| 澳股单股 | ``BHP.AX`` | 后缀 ``.AX`` |
| 英国 FTSE 100 | ``^FTSE`` | 指数 |
| 英股单股 | ``BARC.L`` | 后缀 ``.L`` |
| 印度 NSE | ``^NSEI`` / ``RELIANCE.NS`` | 后缀 ``.NS`` / ``.BO`` |
| 加拿大 TSX | ``^GSPTSE`` / ``SHOP.TO`` | 后缀 ``.TO`` |
| 巴西 Bovespa | ``^BVSP`` / ``VALE3.SA`` | 后缀 ``.SA`` |
| 法国 CAC 40 | ``^FCHI`` / ``BNP.PA`` | 后缀 ``.PA`` |

更多市场后缀见 Yahoo Finance 官方列表。

**timeframe 支持** + Yahoo 的窗口限制：

- ``1m``  ：仅近 7 天
- ``5m`` / ``15m`` / ``30m`` / ``1h``：仅近 60 天
- ``1d`` / ``1wk`` / ``1mo``：全历史
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "yfinance"

#: yfinance 的 interval 字符串 → 估算秒数（backfill 限速估算用）
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1d": 86400,
    "1wk": 604800,
    "1mo": 2_592_000,
}

#: Inalpha 内部 timeframe → yfinance ``interval`` 参数
_TIMEFRAME_TO_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",  # yfinance 用 60m 不是 1h
    "1d": "1d",
    "1wk": "1wk",
    "1mo": "1mo",
}


class YfinanceConnector:
    """yfinance ``Ticker.history`` 包装。"""

    def __init__(self) -> None:
        # yfinance 无客户端对象需持有；保留 init 钩子方便后续加 cookie / proxy
        pass

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 Yahoo Finance 拉 OHLCV。

        Args:
            symbol: Yahoo ticker 原文，含市场后缀。例 ``"6758.T"`` / ``"^N225"``
            timeframe: 见 ``TIMEFRAME_SECONDS`` 8 档
            since: UTC datetime；yfinance ``start`` 接 ISO 字串
            limit: 截断尾部（yfinance 不接 limit）

        Returns:
            list of ``(ts, open, high, low, close, volume)``，UTC aware。
        """
        if timeframe not in _TIMEFRAME_TO_INTERVAL:
            raise ValueError(f"yfinance: unsupported timeframe {timeframe!r}")

        interval = _TIMEFRAME_TO_INTERVAL[timeframe]
        _logger.debug(
            "yfinance_fetch_bars",
            symbol=symbol,
            timeframe=timeframe,
            interval=interval,
            since=since.isoformat(),
            limit=limit,
        )

        rows = await asyncio.to_thread(
            _fetch_sync,
            symbol=symbol,
            interval=interval,
            since=since,
        )

        out: list[tuple[datetime, float, float, float, float, float]] = []
        for ts_raw, o, h, low, c, v in rows:
            ts = _normalize_ts(ts_raw)
            out.append(
                (
                    ts,
                    float(o) if o is not None else 0.0,
                    float(h) if h is not None else 0.0,
                    float(low) if low is not None else 0.0,
                    float(c) if c is not None else 0.0,
                    float(v) if v is not None else 0.0,
                )
            )

        if limit and len(out) > limit:
            out = out[-limit:]
        return out

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        """实时拉 ``symbol`` 的最新成交价（``Ticker.fast_info``）。

        Returns:
            ``(ts, last_price)``，``ts`` UTC aware。Yahoo 的 ``fast_info`` 不直接给
            报价时间，**用 ``datetime.now(UTC)`` 兜底**（caller 看到 stale_seconds≈0）；
            如果 ``last_price`` 缺失会抛 ``ValueError``。

        坑：
        - ``fast_info`` 是 yfinance 0.2.32+ 加的轻量接口；比 ``.info`` 快 10x，
          但仍走 Yahoo HTTP，单次 ~300-800ms，**不要**在循环里高频调
        - 非交易时段 Yahoo 返上一交易日收盘价 + 当前时间戳（上层 is_stale 阈值 5min
          会标 stale=true，符合预期）
        - 部分场外 / 已退市 ticker 会返 ``last_price=None`` —— 抛 ValueError 让上层
          返 5xx，不要静默 fallback DB（caller 拿到错误自己决定）
        """
        last_price = await asyncio.to_thread(_fetch_ticker_sync, symbol)
        if last_price is None:
            raise ValueError(f"yfinance ticker for {symbol} has no last_price (delisted / OTC?)")
        return datetime.now(UTC), float(last_price)

    async def fetch_news(
        self,
        symbol: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """拉 ``symbol`` 的最新新闻头条（yfinance ``Ticker.news``，零 key）。

        Args:
            symbol: Yahoo ticker（``AAPL`` / ``^GSPC`` / ``005930.KS`` 等）；指数也行
            limit: 最多返回多少条；yfinance 一次最多~30 条

        Returns:
            list of dict，每条含 ``{title, publisher, link, published_at(UTC), summary}``；
            空列表表示该 ticker 没新闻（小盘股 / 指数 / 退市常见）。

        坑：
        - yfinance.news 走 Yahoo Finance 内部 API；反爬偶发 429
        - 返回字段在不同 yfinance 版本有差异（``providerPublishTime`` / ``pubDate``）；
          做了字段兼容
        - 新闻按发布时间倒序，**最新的在 list[0]**
        - 内容由 Yahoo 内部聚合，覆盖度比 NewsAPI 弱但零 key
        """
        rows = await asyncio.to_thread(_fetch_news_sync, symbol, limit)
        return rows

    async def close(self) -> None:
        return None


def _fetch_news_sync(symbol: str, limit: int) -> list[dict[str, Any]]:
    """同步调 ``yf.Ticker(symbol).news``，标准化输出。

    yfinance 0.2.x 给的字段结构（最新版兼容旧版）::

        {
          'uuid': '...',
          'title': '...',
          'publisher': 'Reuters',
          'link': 'https://...',
          'providerPublishTime': 1716163200,   # unix seconds
          'type': 'STORY',
          'relatedTickers': ['AAPL', ...],
        }

    更新版可能改为嵌套 ``content`` 字段。本函数两种都兼容。
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    try:
        raw_news = ticker.news or []
    except Exception:  # noqa: BLE001 — 反爬偶发，让上层兜底返空
        _logger.warning("yfinance_news_fetch_failed", symbol=symbol)
        return []

    out: list[dict[str, Any]] = []
    for item in raw_news[:limit]:
        if not isinstance(item, dict):
            continue
        # 新版嵌套结构兼容
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = content.get("title") or item.get("title") or ""
        publisher = (
            content.get("provider", {}).get("displayName")
            if isinstance(content.get("provider"), dict)
            else None
        ) or content.get("publisher") or item.get("publisher") or ""
        # 链接：新旧版字段不同
        link_obj = content.get("clickThroughUrl") or content.get("canonicalUrl")
        link = (
            link_obj.get("url") if isinstance(link_obj, dict) else None
        ) or content.get("link") or item.get("link") or ""
        # 时间戳：unix seconds（旧）/ ISO 8601（新）
        ts_raw = (
            content.get("pubDate")
            or content.get("displayTime")
            or item.get("providerPublishTime")
        )
        published_at = _normalize_news_ts(ts_raw)
        summary = content.get("summary") or item.get("summary") or ""

        if not title:
            continue
        out.append(
            {
                "title": title,
                "publisher": publisher,
                "link": link,
                "published_at": published_at.isoformat() if published_at else None,
                "summary": summary[:500] if summary else "",
            }
        )
    return out


def _normalize_news_ts(ts_raw: Any) -> datetime | None:
    """yfinance news ts 是 unix seconds（int）或 ISO string，统一成 UTC aware datetime。"""
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        try:
            return datetime.fromtimestamp(int(ts_raw), tz=UTC)
        except (ValueError, OSError):
            return None
    if isinstance(ts_raw, str):
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _fetch_sync(
    *,
    symbol: str,
    interval: str,
    since: datetime,
) -> list[tuple[Any, Any, Any, Any, Any, Any]]:
    """同步调 yfinance.Ticker(symbol).history。

    抽函数让 ``asyncio.to_thread`` 序列化参数，并方便测试 monkeypatch。
    返回原始 (ts, o, h, l, c, v) tuple list，上层做类型归一化。
    """
    import yfinance as yf

    # yfinance 接 start 字符串 'YYYY-MM-DD'；分钟级窗口会被 yfinance 自身收紧
    start_str = since.strftime("%Y-%m-%d")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_str, interval=interval, auto_adjust=False, raise_errors=False)
    if df is None or len(df) == 0:
        return []
    # DataFrame index 是 ts；columns: Open / High / Low / Close / Volume / Dividends / Stock Splits
    return [
        (idx, row.get("Open"), row.get("High"), row.get("Low"), row.get("Close"), row.get("Volume"))
        for idx, row in df.iterrows()
    ]


def _fetch_ticker_sync(symbol: str) -> float | None:
    """同步调 ``yf.Ticker(symbol).fast_info['last_price']``。

    抽函数让 ``asyncio.to_thread`` 序列化参数 + 方便测试 monkeypatch。
    fast_info 字段名各 yfinance 版本有差异（``last_price`` / ``lastPrice`` /
    ``regular_market_price``），全试一遍取首个非 None。
    """
    import yfinance as yf

    fi = yf.Ticker(symbol).fast_info
    for key in ("last_price", "lastPrice", "regular_market_price", "regularMarketPrice"):
        try:
            val = fi[key] if hasattr(fi, "__getitem__") else getattr(fi, key, None)
        except (KeyError, AttributeError):
            val = None
        if val is not None:
            return float(val)
    return None


def _normalize_ts(ts_raw: Any) -> datetime:
    """yfinance 给的 ts 是 ``pd.Timestamp``（可能含 / 不含 tz），统一成 UTC aware。"""
    # pd.Timestamp 有 to_pydatetime
    if hasattr(ts_raw, "to_pydatetime"):
        dt = ts_raw.to_pydatetime()
    elif isinstance(ts_raw, datetime):
        dt = ts_raw
    else:
        dt = datetime.fromisoformat(str(ts_raw))

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    # 已有 tz 的（如 America/New_York）统一转 UTC
    return dt.astimezone(UTC)


# ---------- module-level singleton ----------

_connector: YfinanceConnector | None = None


def init_connector() -> YfinanceConnector:
    """启动时调一次。yfinance 无 key，无失败路径。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("Yfinance connector already initialized")
    _connector = YfinanceConnector()
    register_connector(VENUE, _connector)
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    unregister_connector(VENUE)
    _connector = None


def get_connector() -> YfinanceConnector:
    if _connector is None:
        raise RuntimeError("Yfinance connector not initialized; call init_connector() first")
    return _connector

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
import functools
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "yfinance"

# Yahoo 反爬：一波并发 history 请求会触发 429，部分标的静默返残缺/空数据（实测串行
# 全 24 根、5 标的并发 gather 时 MSFT/META 只回 4 根）。进程级串行 + 最小间隔把并发
# 突发摊成节流串行，避免 429 残缺（akshare 同类防封思路）。其它 venue 不受影响。
_FETCH_LOCK = asyncio.Lock()
_MIN_FETCH_INTERVAL_S = 0.3
#: 锁内单次 history 超时上限——TCP 挂起时快速放锁,不把整个 panel 拖到 60s
_FETCH_TIMEOUT_S = 30.0
_last_fetch_mono = 0.0
#: yfinance 专属有界线程池。wait_for 超时只取消 asyncio task,底层同步线程**不可取消**、
#: 会继续持 Yahoo 连接到 TCP 真正超时——用独立有界池隔离这些"孤儿线程",避免耗尽 asyncio
#: 默认共享 executor(FRED / 其它 to_thread 不受影响);配合 _FETCH_LOCK(同时只 1 个在飞)足够。
#: **池满降级(已知,可接受)**:Yahoo 整体不响应(IP 被封)时,孤儿线程逐个累积(锁串行 →
#: 每 _FETCH_TIMEOUT_S 一个),填满 4 槽后新的 run_in_executor 会排队,panel 取数退化到分钟级
#: ——但**仅影响 yfinance panel 这条失败路径**,FRED / 单标的 / 其它 venue 仍健康。真要消除
#: 需 _fetch_sync 内置 socket timeout 让线程自行退出(yfinance.history 未暴露,留后续)。
_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yfinance")

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

        try:
            rows = await self._throttled_fetch_sync(
                symbol=symbol, interval=interval, since=since
            )
        except Exception as exc:
            _logger.warning(
                "yfinance_fetch_bars_failed",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )
            return []

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

    @staticmethod
    async def _throttled_fetch_sync(
        *, symbol: str, interval: str, since: datetime
    ) -> list[tuple[Any, Any, Any, Any, Any, Any]]:
        """串行 + 最小间隔跑 yfinance history，防并发突发触发 Yahoo 429 返残缺数据。

        进程级 ``_FETCH_LOCK`` 保证同一时刻只有一个 history 在飞；锁内再补足
        ``_MIN_FETCH_INTERVAL_S`` 的最小间隔。panel 多标的并发 gather 时各标的会在此
        排队节流，换回完整 bar（正确性优先于这点串行延迟）。

        **锁内每请求超时 ``_FETCH_TIMEOUT_S``**：``raise_errors=False`` 对 HTTP 4xx/5xx
        免疫，但 TCP 层无响应（Yahoo 封 IP / 网络分区）会让 to_thread 挂起、锁被持有，
        把整个 panel 拖到 HTTP client 60s 超时才释放。这里加 wait_for 上限，单标的卡住
        只丢它自己、快速放锁让队列继续（被取消的线程自然收尾，不再阻塞后续标的）。
        """
        global _last_fetch_mono
        async with _FETCH_LOCK:
            # sleep 也包进 try：若在 sleep 处被外部 cancel（上层请求超时），finally 仍更新
            # _last_fetch_mono，否则下一个 symbol 进锁读到陈旧时间戳会跳过节流、与上一个
            # 孤儿请求并发打 Yahoo 触发 429。
            try:
                wait = _MIN_FETCH_INTERVAL_S - (time.monotonic() - _last_fetch_mono)
                if wait > 0:
                    await asyncio.sleep(wait)
                loop = asyncio.get_running_loop()
                return await asyncio.wait_for(
                    loop.run_in_executor(
                        _FETCH_EXECUTOR,
                        functools.partial(
                            _fetch_sync, symbol=symbol, interval=interval, since=since
                        ),
                    ),
                    timeout=_FETCH_TIMEOUT_S,
                )
            finally:
                _last_fetch_mono = time.monotonic()

    async def fetch_ticker(self, symbol: str) -> tuple[datetime, float]:
        """实时拉 ``symbol`` 的最新成交（1m history 最后一根 bar）。

        Returns:
            ``(ts, last_price)``，``ts`` UTC aware——是**真实成交分钟**而非本地 now()。
            休市时段最后一根 bar 停在上个交易时段 → 上层 stale_seconds 反映真实滞后、
            ``is_stale=true``（issue #62：原 fast_info + now() 兜底让休市恒"新鲜"，
            paper live runner 会按几小时前的陈价下单）。无任何 bar 抛 ``RuntimeError``。

        坑：
        - 走 Yahoo chart HTTP，单次 ~300-800ms，**不要**在循环里高频调
        - ``prepost=True``：盘前盘后有真实成交时按延伸时段最新成交判新鲜
        - 部分场外 / 已退市 ticker 无 1m 数据 —— 抛 RuntimeError
        - Yahoo 对非美 IP 反爬偏严；TCP 挂起 / 429 / 空返都统一成 RuntimeError，
          上层 `/ticker` 端点捕获后返 TICKER_UNAVAILABLE（502）而非裸 500
        """
        try:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _FETCH_EXECUTOR,
                    functools.partial(_fetch_ticker_sync, symbol),
                ),
                timeout=_FETCH_TIMEOUT_S,
            )
        except TimeoutError:
            raise RuntimeError(
                f"yfinance ticker for {symbol} timed out after {_FETCH_TIMEOUT_S}s "
                "(Yahoo may be blocking this IP — try fresh=false to use DB cache)"
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"yfinance ticker for {symbol} unavailable: {exc}"
            ) from exc

        if result is None:
            raise RuntimeError(
                f"yfinance ticker for {symbol} has no 1m bars (delisted / OTC / "
                "rate-limited — Yahoo may have returned empty data)"
            )
        ts, last_price = result
        return ts, float(last_price)

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

    async def fetch_financials(
        self, symbol: str, as_of: str | None = None
    ) -> dict[str, Any]:
        """拉 Yahoo Finance 财报基本面数据。

        Uses ``yf.Ticker(symbol).info`` for key metrics +
        ``.quarterly_financials`` for revenue/earnings.

        Returns standardized dict with same structure as akshare version.

        ``as_of``（ADR-0053 阶段 A）：接受参数以与 akshare 端签名对称，但 **v1 不对 yfinance
        财报做 PIT 截断**（yfinance .info 只给"最新"快照、不带历史报告期列，无法可靠按
        as_of 回溯）；如需 yfinance 财报 PIT 须改用带报告期的数据源，留后续。
        """
        _logger.debug("yfinance_fetch_financials", symbol=symbol, as_of=as_of)

        try:
            result = await asyncio.to_thread(_fetch_financials_sync, symbol)
        except Exception as exc:
            _logger.warning("yfinance_financials_fetch_failed", symbol=symbol, error=str(exc))
            return {
                "venue": VENUE,
                "symbol": symbol,
                "available": False,
                "reason": f"yfinance fetch failed: {exc}",
            }

        if result is None or (isinstance(result, dict) and not result):
            return {
                "venue": VENUE,
                "symbol": symbol,
                "available": False,
                "reason": "yfinance returned empty financial data",
            }
        # PIT 防误用（#100 CR）：yfinance v1 不按 as_of 截断，但响应 as_of 字段回显的是
        # 取数时刻(now)——调用方若对 yfinance 传 as_of 做回测,会把当前快照当历史用 = 静默
        # 未来函数。给 as_of 时显式写 reason 提示 PIT 未生效,让调用方能据此察觉、别误信。
        if as_of is not None and isinstance(result, dict):
            result["reason"] = (
                "yfinance PIT not supported in v1; indicators reflect current snapshot, "
                "not requested as_of"
            )
        return result

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
    except Exception:
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


def _fetch_financials_sync(symbol: str) -> dict[str, Any]:
    """同步调 yfinance 财报接口 —— ``Ticker.info`` + ``quarterly_financials``。

    映射 yfinance 字段名到标准化 indicator 名称。
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info: dict[str, Any] = {}
    try:
        info = ticker.info or {}
    except Exception:
        pass

    if not info:
        return {}

    indicators: dict[str, float | None] = {}
    # yfinance info 字段 → 标准化指标名
    _field_map = {
        "marketCap": "market_cap",
        "trailingPE": "pe_ratio",
        "forwardPE": "pe_ratio",
        "priceToBook": "pb_ratio",
        "returnOnEquity": "roe",
        "revenueGrowth": "revenue_yoy",
        "earningsGrowth": "profit_yoy",
        "grossMargins": "gross_margin",
        "profitMargins": "net_margin",
        "debtToEquity": "debt_to_equity",
        # 财务质量项（红旗检查：现金流 vs 利润、偿债能力）；info 缺字段时静默跳过
        "operatingCashflow": "operating_cashflow",
        "freeCashflow": "free_cashflow",
        "totalCash": "total_cash",
        "totalDebt": "total_debt",
        "currentRatio": "current_ratio",
        "quickRatio": "quick_ratio",
    }
    for yf_key, norm_key in _field_map.items():
        val = info.get(yf_key)
        if val is not None:
            try:
                indicators[norm_key] = float(val)
            except (TypeError, ValueError):
                pass

    # 若 info 里 roe 以小数存（yfinance 行为不一致），防御性转换
    roe = indicators.get("roe")
    if roe is not None and roe > 10:
        indicators["roe"] = roe / 100.0

    from datetime import datetime

    return {
        "venue": VENUE,
        "symbol": symbol,
        "available": True,
        "as_of": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "indicators": indicators,
        "raw": info,
    }


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


def _fetch_ticker_sync(symbol: str) -> tuple[datetime, float] | None:
    """同步拉最新成交：``history(period='1d', interval='1m', prepost=True)`` 最后一根有价 bar。

    抽函数让 ``asyncio.to_thread`` 序列化参数 + 方便测试 monkeypatch。
    不用 fast_info：它只给价不给报价时间，休市时段会把"几小时前的收盘价"伪装成
    刚发生的（issue #62）；1m history 的 bar index 就是真实成交分钟，一次 HTTP
    同时拿到价和时间。``period='1d'`` 休市时 Yahoo 返最近一个交易日的 bars。
    """
    import yfinance as yf

    df = yf.Ticker(symbol).history(
        period="1d", interval="1m", prepost=True, auto_adjust=False, raise_errors=False
    )
    if df is None or len(df) == 0 or "Close" not in df:
        return None
    closes = df["Close"].dropna()
    if len(closes) == 0:
        return None
    return _normalize_ts(closes.index[-1]), float(closes.iloc[-1])


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


# ---------- yfinance proxy (env YFINANCE_PROXY_URL) ----------

#: Yahoo 主机 → CF Worker 路径前缀 key（与 infra/workers/yahoo-proxy.js 的 HOST_MAP 对应）
_YAHOO_PROXY_HOSTS: tuple[str, ...] = (
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "finance.yahoo.com",
    "fc.yahoo.com",
)


def _rewrite_yahoo_url(url: str, proxy_base: str) -> str:
    """把 ``https://<yahoo-host>/...`` 改写成 ``{proxy_base}/<host-key>/...``。

    非 Yahoo URL 原样返回。``proxy_base`` 应已去尾 ``/``。
    """
    if "yahoo.com" not in url:
        return url
    for host in _YAHOO_PROXY_HOSTS:
        if host in url:
            key = host.split(".")[0]
            return url.replace(f"https://{host}", f"{proxy_base}/{key}")
    return url


def _install_yfinance_proxy(proxy_url: str) -> None:
    """Monkey-patch HTTP 客户端的 ``Session.request``，让 yfinance 全量走代理。

    在 ``init_connector()`` 里调一次——仅当 ``YFINANCE_PROXY_URL`` 有值时生效。
    拦截 ``query1/query2/finance/fc.yahoo.com`` → 重写为 ``{proxy_url}/{host_key}/...``。
    使用 Cloudflare Worker 等边缘代理时，出口 IP 为美国段，Yahoo 不拦。

    **必须打中 ``curl_cffi``**：yfinance 0.2.51+ / 1.x 全量 HTTP 走
    ``curl_cffi.requests.Session(impersonate="chrome")``，并**主动拒绝**注入的非 curl_cffi
    session（``data.py``）。历史上此处只 patch 了标准库 ``requests.Session``——yfinance 根本
    不碰它，导致代理静默空转、请求仍从被封 IP 直连 Yahoo。这里两个库都 patch，以 curl_cffi
    为主、标准库为兜底（旧版 yfinance / 其它依赖仍可能用到）；两者 ``Session.request`` 位置
    签名一致 ``(self, method, url, **kwargs)``，改写逻辑共用 ``_rewrite_yahoo_url``。
    """
    proxy_base = proxy_url.rstrip("/")
    patched: list[str] = []

    def _patch_session_class(session_cls: Any, label: str) -> None:
        _original = session_cls.request

        def _proxied(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
            return _original(self, method, _rewrite_yahoo_url(url, proxy_base), *args, **kwargs)

        session_cls.request = _proxied  # type: ignore[method-assign]
        patched.append(label)

    # 主目标：curl_cffi（yfinance 实际使用的 HTTP 库）
    try:
        from curl_cffi.requests import Session as _CurlSession

        _patch_session_class(_CurlSession, "curl_cffi.requests.Session")
    except Exception as exc:
        _logger.warning("yfinance_proxy_curl_cffi_patch_failed", error=str(exc))

    # 兜底：标准库 requests（旧版 yfinance / 其它依赖可能用到）
    try:
        import requests as _requests

        _patch_session_class(_requests.Session, "requests.Session")
    except Exception as exc:
        _logger.warning("yfinance_proxy_requests_patch_failed", error=str(exc))

    if patched:
        _logger.info("yfinance_proxy_installed", proxy_url=proxy_url, patched=patched)
    else:
        # 一个都没打中 = 代理完全无效，请求会直连被封 IP。显式 error，别再假成功。
        _logger.error("yfinance_proxy_install_failed_no_target", proxy_url=proxy_url)


# ---------- module-level singleton ----------

_connector: YfinanceConnector | None = None


def init_connector() -> YfinanceConnector:
    """启动时调一次。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("Yfinance connector already initialized")

    # 可选代理：VPS IP 被 Yahoo 限流时，通过 CF Worker 边缘节点中转
    proxy_url = os.environ.get("YFINANCE_PROXY_URL", "").strip()
    if proxy_url:
        _install_yfinance_proxy(proxy_url)

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

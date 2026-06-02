"""``(venue, symbol)`` → ``exchange_calendars`` 日历 code 解析。

D-9.1a 收尾（多市场 ``MarketHoursRule``）：``InstrumentId.venue`` 是**数据源**
标识（``yfinance`` / ``akshare`` / ``binance``），**不是交易所**。同一 venue 跨多
市场——``yfinance`` 同时服务美股 / 全球指数 / 韩澳印，``akshare`` 同时服务
A股 / 港股 / 日英德。因此必须结合 ``symbol`` 的前缀（``sh.`` / ``sz.`` / ``hk.`` /
``jp.`` / ``uk.`` / ``de.``）或后缀（``.KS`` / ``.AX`` / ``.NS`` / ``.T`` / ``.L`` /
``.DE`` / ``.PA`` / ``.TO`` / ``.SA``）/ 指数前缀（``^``）才能定位真实交易所。

返回 ``exchange_calendars`` 的 calendar code（``XNYS`` / ``XSHG`` / ``XHKG`` …）；
下列情形返 ``None``（由上层 :class:`RoutingCalendar` 特判）：

- **crypto**（binance / coinbase / …）：24/7，无交易日历 → 上层永真
- **fred**：宏观时间序列，无交易时段概念 → 上层放行
- **未识别 venue / 未列入的指数 / 未知后缀的美股以外标的**：fail-open

映射约定来源：``orchestrator.ts`` 市场分类路由表 + ``connectors/akshare.py``
symbol 前缀约定。

注：``exchange_calendars`` 4.x **不提供** 深交所 ``XSHE`` 与印度 NSE ``XNSE``，
分别复用 ``XSHG``（沪深交易时段 / 假日一致）、``XBOM``（BSE，与 NSE 时段 / 假日
基本一致）。
"""
from __future__ import annotations

# crypto venue：24/7，无交易日历（上层 RoutingCalendar 返 True）
_CRYPTO_VENUES: frozenset[str] = frozenset(
    {
        "binance",
        "coinbase",
        "okx",
        "bybit",
        "kraken",
        "kucoin",
        "bitfinex",
        "huobi",
        "gate",
        "mexc",
    }
)

# 走 yfinance / alpaca 的美股 + 全球单股 + 指数数据源
_US_DATA_VENUES: frozenset[str] = frozenset({"yfinance", "alpaca"})

# akshare symbol 前缀 → calendar code（sz. 复用 XSHG）
_AKSHARE_PREFIX_TO_CODE: dict[str, str] = {
    "sh": "XSHG",
    "sz": "XSHG",
    "hk": "XHKG",
    "jp": "XTKS",
    "uk": "XLON",
    "de": "XFRA",
}

# yfinance / alpaca symbol 后缀 → calendar code（.ns 复用 XBOM）。
# 按长度降序匹配，避免 ".to"（加拿大）被 ".t"（日本）误截。
_YF_SUFFIX_TO_CODE: dict[str, str] = {
    ".ks": "XKRX",
    ".ax": "XASX",
    ".ns": "XBOM",
    ".to": "XTSE",
    ".sa": "BVMF",
    ".pa": "XPAR",
    ".de": "XFRA",
    ".l": "XLON",
    ".t": "XTKS",
}

# 全球指数 symbol → 底层市场 calendar code（未列入 → None，fail-open）
_INDEX_TO_CODE: dict[str, str] = {
    "^gspc": "XNYS",
    "^dji": "XNYS",
    "^ixic": "XNYS",
    "^rut": "XNYS",
    "^n225": "XTKS",
    "^ftse": "XLON",
    "^gdaxi": "XFRA",
    "^hsi": "XHKG",
    "^ks11": "XKRX",
    "^axjo": "XASX",
}


def is_crypto_venue(venue: str) -> bool:
    """``venue`` 是否为 crypto 数据源（24/7，无交易日历）。

    给跨模块复用（如 ``currency_resolver``）的公开判定，避免直接导入私有
    ``_CRYPTO_VENUES`` —— 集合改名时这里仍稳定。
    """
    return venue.strip().lower() in _CRYPTO_VENUES


def resolve_calendar_code(venue: str, symbol: str) -> str | None:
    """把 ``(venue, symbol)`` 解析成 ``exchange_calendars`` 日历 code。

    Args:
        venue: ``InstrumentId.venue``（数据源标识，如 ``yfinance`` / ``akshare``）。
        symbol: ``InstrumentId.symbol``（带市场前缀 / 后缀，如 ``sh.600519`` /
            ``7203.T`` / ``^N225``）。

    Returns:
        日历 code（``XNYS`` 等）；crypto / fred / 未识别 / 未知标的 → ``None``。
    """
    v = venue.strip().lower()
    s = symbol.strip().lower()

    if v in _CRYPTO_VENUES:
        return None  # 24/7，上层特判永真
    if v == "fred":
        return None  # 宏观，无交易时段

    if v == "akshare":
        prefix = s.split(".", 1)[0] if "." in s else ""
        return _AKSHARE_PREFIX_TO_CODE.get(prefix)

    if v in _US_DATA_VENUES:
        if s.startswith("^"):
            return _INDEX_TO_CODE.get(s)  # 未列入指数 → None（fail-open）
        for suffix in sorted(_YF_SUFFIX_TO_CODE, key=len, reverse=True):
            if s.endswith(suffix):
                return _YF_SUFFIX_TO_CODE[suffix]
        return "XNYS"  # 无后缀 = 美股

    return None  # 未识别 venue → fail-open


__all__ = ["is_crypto_venue", "resolve_calendar_code"]

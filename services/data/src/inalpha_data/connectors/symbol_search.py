"""公司名 / 关键词 → ticker 解析 connector。

候选池构建的硬需求：agent 从新闻里拿到的是公司名，要落到 get_bars /
get_fundamentals 必须先有 symbol——靠 LLM 训练记忆猜代码会撞时效性纪律
（记忆可能 stale / 编错），所以给一条可溯源的解析路径。

两个来源（零 key）：
- akshare ``stock_info_a_code_name()``：A股全量 code/name 表。进程内缓存
  （TTL 1 天，表一天内不变），子串匹配中文名 / 代码前缀。
  输出 symbol 与本服务 akshare connector 约定一致（``sh.600519`` / ``sz.000001``）。
- yfinance ``Search``：Yahoo 全球检索（美 / 港 / 日韩欧等），输出原生 yahoo
  symbol（``AAPL`` / ``0700.HK``），与 yfinance connector 直接可用。

venue="auto"：query 含 CJK → 先 A股表再 yahoo；纯 ASCII → 只走 yahoo。
失败语义：尽力而为，异常一律返 []。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from inalpha_shared import get_logger

VENUE = "symbols"
_logger = get_logger(__name__)

_A_SHARE_TABLE_TTL_S = 86_400


def _a_share_prefix(code: str) -> str | None:
    """6 开头 → sh；0/3 开头 → sz；其余（北交所 4/8 等）暂不支持返 None。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return None


def _load_a_share_table_sync() -> list[dict[str, str]]:
    import akshare as ak

    raw = ak.stock_info_a_code_name()
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        return []
    return raw.to_dict(orient="records")  # type: ignore[no-any-return]


def _yahoo_search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    import yfinance as yf

    try:
        search = yf.Search(query, max_results=max_results, news_count=0)
        return list(search.quotes or [])
    except Exception as exc:
        _logger.warning("symbol_search_yahoo_failed", query=query[:80], error=str(exc))
        return []


class SymbolSearchConnector:
    def __init__(self) -> None:
        self._a_share_cache: list[dict[str, str]] | None = None
        self._a_share_loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def search(
        self, query: str, venue: str = "auto", max_results: int = 10
    ) -> list[dict[str, Any]]:
        """返回 [{symbol, name, exchange, venue, quote_type}]，按来源相关性排序。"""
        query = query.strip()
        if not query:
            return []
        has_cjk = any("一" <= c <= "鿿" for c in query)

        results: list[dict[str, Any]] = []
        try:
            if venue in ("akshare", "auto") and (venue == "akshare" or has_cjk):
                results.extend(await self._search_a_share(query, max_results))
            if venue in ("yfinance", "auto") and len(results) < max_results:
                results.extend(
                    await self._search_yahoo(query, max_results - len(results))
                )
        except Exception as exc:
            _logger.warning("symbol_search_error", query=query[:80], error=str(exc))
        return results[:max_results]

    async def _search_a_share(self, query: str, max_results: int) -> list[dict[str, Any]]:
        table = await self._get_a_share_table()
        out: list[dict[str, Any]] = []
        q = query.lower()
        for row in table:
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if q in name.lower() or code.startswith(query):
                prefix = _a_share_prefix(code)
                if prefix is None:
                    continue
                out.append(
                    {
                        "symbol": f"{prefix}.{code}",
                        "name": name,
                        "exchange": "XSHG" if prefix == "sh" else "XSHE",
                        "venue": "akshare",
                        "quote_type": "EQUITY",
                    }
                )
                if len(out) >= max_results:
                    break
        return out

    async def _search_yahoo(self, query: str, max_results: int) -> list[dict[str, Any]]:
        if max_results <= 0:
            return []
        quotes = await asyncio.to_thread(_yahoo_search_sync, query, max_results)
        out: list[dict[str, Any]] = []
        for q in quotes:
            symbol = q.get("symbol")
            if not symbol:
                continue
            out.append(
                {
                    "symbol": str(symbol),
                    "name": str(q.get("shortname") or q.get("longname") or ""),
                    "exchange": str(q.get("exchange") or ""),
                    "venue": "yfinance",
                    "quote_type": str(q.get("quoteType") or ""),
                }
            )
        return out

    async def _get_a_share_table(self) -> list[dict[str, str]]:
        async with self._lock:
            now = time.monotonic()
            if (
                self._a_share_cache is not None
                and now - self._a_share_loaded_at < _A_SHARE_TABLE_TTL_S
            ):
                return self._a_share_cache
            try:
                table = await asyncio.to_thread(_load_a_share_table_sync)
            except Exception as exc:
                _logger.warning("symbol_search_a_share_table_failed", error=str(exc))
                # 拉表失败保留旧缓存（如有）——可用性优先于新鲜度（表本身极少变）
                return self._a_share_cache or []
            self._a_share_cache = table
            self._a_share_loaded_at = now
            return table

    async def close(self) -> None:
        return None


# ---------- module-level singleton ----------

_connector: SymbolSearchConnector | None = None


def init_connector() -> SymbolSearchConnector:
    global _connector
    if _connector is not None:
        raise RuntimeError("SymbolSearch connector already initialized")
    _connector = SymbolSearchConnector()
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    _connector = None


def get_connector() -> SymbolSearchConnector:
    if _connector is None:
        raise RuntimeError("SymbolSearch connector not initialized; call init_connector() first")
    return _connector

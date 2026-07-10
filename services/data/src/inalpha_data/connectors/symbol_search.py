"""公司名 / 关键词 → ticker 解析 connector。

候选池构建的硬需求：agent 从新闻里拿到的是公司名，要落到 get_bars /
get_fundamentals 必须先有 symbol——靠 LLM 训练记忆猜代码会撞时效性纪律
（记忆可能 stale / 编错），所以给一条可溯源的解析路径。

两个来源（零 key）：
- baostock ``query_stock_basic()``：A股全量 code/name 表（证券宝开源接口）。
  进程内缓存（TTL 1 天，表一天内不变），子串匹配中文名 / 代码前缀。
  输出 symbol 格式与本服务约定一致（``sh.600519`` / ``sz.000001``）。
- yfinance ``Search``：Yahoo 全球检索（美 / 港 / 日韩欧等），输出原生 yahoo
  symbol（``AAPL`` / ``0700.HK``），与 yfinance connector 直接可用。

venue="auto"：query 含 CJK → A股表与 yahoo **并行都查、轮替合并**；纯 ASCII →
只走 yahoo。语言只决定"是否多查一路 A股"，**不决定市场**——中文用户问美股 /
港股公司（中文名）极常见，串行补位会让 A股结果挤掉 yahoo 候选、跨市场同名
歧义时单边呈现（§3 不预设语言/市场）。

失败语义：尽力而为，异常一律返 []。
"""

from __future__ import annotations

import asyncio
import time
from itertools import zip_longest
from typing import Any

from inalpha_shared import get_logger

VENUE = "symbols"
_logger = get_logger(__name__)

_A_SHARE_TABLE_TTL_S = 86_400
# Yahoo Search 内部是同步 HTTP 且无 timeout：用 wait_for 包 to_thread，慢响应时
# 返空而不是长期占住 ThreadPoolExecutor 工作线程（对齐 web_fetch 的超时防线）。
_YAHOO_SEARCH_TIMEOUT_S = 5.0


def _a_share_prefix(code: str) -> str | None:
    """6 开头 → sh；0/3 开头 → sz；其余（北交所 4/8 等）暂不支持返 None。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return None


def _load_a_share_table_sync() -> list[dict[str, str]]:
    """从 baostock 拉A股全量 code/name 表（替代 akshare）。

    baostock 是证券宝开源接口，已用于 A股日 K 线（更稳定）。
    query_stock_basic() 返回全市场证券列表（股票+指数+ETF等），过滤只保留
    上市股票（type='1', status='1'）。code 已带前缀（sh./sz.）。

    baostock 限制：日 K 及以上频率、每日 5 万次请求上限、禁止并发连接。
    """
    import baostock as bs

    try:
        lg = bs.login()
        if lg.error_code != "0":
            _logger.warning("symbol_search_baostock_login_failed", error=lg.error_msg)
            return []
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            _logger.warning("symbol_search_baostock_query_failed", error=rs.error_msg)
            return []
        data_list: list[list[str]] = []
        while (rs.error_code == "0") & rs.next():
            data_list.append(rs.get_row_data())
    except Exception as exc:
        _logger.warning("symbol_search_baostock_failed", error=str(exc))
        return []
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    # 字段：[code, name, list_date, delist_date, type, status]
    # type='1'=股票, type='2'=指数; status='1'=上市, status='0'=退市
    # code 已带前缀（sh.600519 / sz.000001），需去掉前缀再判断深沪
    out: list[dict[str, str]] = []
    for row in data_list:
        if len(row) < 6:
            continue
        code, name, _, _, typ, status = row[:6]
        # 只保留上市股票（过滤指数、ETF、退市股）
        if typ != "1" or status != "1":
            continue
        # 去掉前缀（sh./sz.）得到纯代码
        pure_code = code.split(".", 1)[-1] if "." in code else code
        if not pure_code:
            continue
        out.append({"code": pure_code, "name": str(name)})
    return out


def _merge_round_robin(
    a: list[dict[str, Any]], b: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """两来源轮替合并（a1, b1, a2, b2, ...），保留各自内部相关性排序。"""
    out: list[dict[str, Any]] = []
    for x, y in zip_longest(a, b):
        if x is not None:
            out.append(x)
        if y is not None:
            out.append(y)
    return out


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
            if venue == "baostock":
                results = await self._search_a_share(query, max_results)
            elif venue == "yfinance" or not has_cjk:
                results = await self._search_yahoo(query, max_results)
            else:
                # auto + CJK：两路并行都查、轮替合并。A股表是本地缓存、yahoo 有
                # 5s 超时，并行无额外延迟；轮替保证任一来源不会把另一来源挤出
                # max_results（跨市场同名歧义时两边都呈现，由 agent 按 venue 选）
                a_share, yahoo = await asyncio.gather(
                    self._search_a_share(query, max_results),
                    self._search_yahoo(query, max_results),
                )
                results = _merge_round_robin(a_share, yahoo)
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
                        "venue": "baostock",  # 数据源为 baostock
                        "quote_type": "EQUITY",
                    }
                )
                if len(out) >= max_results:
                    break
        return out

    async def _search_yahoo(self, query: str, max_results: int) -> list[dict[str, Any]]:
        if max_results <= 0:
            return []
        try:
            quotes = await asyncio.wait_for(
                asyncio.to_thread(_yahoo_search_sync, query, max_results),
                timeout=_YAHOO_SEARCH_TIMEOUT_S,
            )
        except TimeoutError:
            _logger.warning(
                "symbol_search_yahoo_timeout", query=query[:80], timeout_s=_YAHOO_SEARCH_TIMEOUT_S
            )
            return []
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

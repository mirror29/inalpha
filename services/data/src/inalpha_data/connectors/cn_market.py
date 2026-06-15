"""A股市场级行情归因数据 connector —— 直连东财 / 同花顺公开接口。

行情归因（"今天为什么涨/跌"）需要的市场级数据，全部**无需 symbol**：
- 全市场财经快讯（东财 7×24 全球资讯）
- 行业板块涨跌幅榜（东财 push2，一次请求拿全 ~500 个板块）
- 沪深港通资金分钟流向（同花顺，交易所 2024-08 停止北向盘中披露后
  仍有同花顺自己的估算口径数值）
- 当日强势股 + 人工题材标签（同花顺，归因"什么主线在涨"的最直接证据）

接口配方移植自 simonlin1212/a-stock-data（Apache-2.0，v3.2.2，
https://github.com/simonlin1212/a-stock-data）——直连一手源比 akshare 封装
快且可控（实测 akshare 财联社接口本地挂死、板块榜翻页 5s+）。端点改版失效时
先查上游新 release。

防封纪律（同样移植自上游实测结论）：
- 每 host 串行（asyncio.Lock），请求间隔 ≥ min_interval + 0.1~0.5s 随机抖动
- AsyncClient 复用（Keep-Alive，不重复建连）
- 常规浏览器 UA + 源站 Referer
- 触发 403/429 时**显式抛错**而非静默空——"引擎故障"被吞成空数组正是
  D-12+ 行情归因修复要消灭的事故模式

失败语义：与 web_search（尽力而为，失败带 status 返回）不同，市场级数据是
归因的**结论级输入**，拿不到必须上抛 ``CnMarketError``，由 API 层转 502。
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from inalpha_shared import get_logger

from ..config import get_data_settings

VENUE = "cn_market"
_logger = get_logger(__name__)

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

_EM_FASTNEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
_EM_BOARD_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_THS_HARDEN_URL = "https://zx.10jqka.com.cn/event/api/getharden/date/{date}/"
_THS_HSGT_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"

NORTHBOUND_NOTE = (
    "沪深港通净买入为同花顺估算口径（亿元，累计值，负数=净流出）；"
    "交易所自 2024-08 起不再盘中披露北向官方数据，本数值仅供方向参考"
)


class CnMarketError(RuntimeError):
    """市场级数据源失败（网络 / 反爬 / 改版）。API 层转 502，不静默。"""


def _bj_to_utc(value: str) -> datetime | None:
    """``"2026-06-12 15:37:31"``（北京时间）→ aware UTC datetime。解析失败返 None。"""
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=_TZ_SHANGHAI).astimezone(UTC)


class CnMarketConnector:
    """东财 / 同花顺市场级数据。零 key；大陆源直连（不走系统代理）。"""

    def __init__(self) -> None:
        s = get_data_settings()
        self._timeout = s.cn_market_timeout_s
        self._min_interval = s.cn_market_min_interval_s
        self._cache_ttl = s.cn_market_cache_ttl_s
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        # 每 host 一把锁 + 上次请求时间戳：串行 + 限速（防封铁律①②）
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        # 大陆源直连：本地代理常把出站路由到境外出口，反而连不上/超时。
        # follow_redirects=True：源站把端点迁到 HTTPS-only / 换路径时自动跟随 301/302，
        # 否则 httpx 默认拿到重定向 HTML，resp.json() 抛 ValueError 会用 "non-JSON"
        # 掩盖真实原因（是 redirect 而非 API 改版），干扰按报错跟进上游修复。
        # 这些 host 是固定公开 API（非用户输入 URL），无 SSRF 风险。
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            trust_env=False,
            follow_redirects=True,
            headers={"User-Agent": _UA},
        )

    # ── 四个市场级数据方法 ──────────────────────────────────────────

    async def fetch_market_news(self, limit: int = 20) -> list[dict[str, Any]]:
        """东财 7×24 全球财经快讯（无需 symbol）。

        Returns: ``[{title, summary, published_at, related_codes}]``，
        published_at 为 UTC ISO 字符串。
        """
        cached = self._cache_get(("news", limit))
        if cached is not None:
            return cached
        data = await self._get_json(
            "np-weblist.eastmoney.com",
            _EM_FASTNEWS_URL,
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": str(min(max(limit, 1), 50)),
                "req_trace": uuid.uuid4().hex,
            },
            headers={"Referer": "https://kuaixun.eastmoney.com/"},
        )
        raw_list = (data.get("data") or {}).get("fastNewsList")
        if not isinstance(raw_list, list):
            raise CnMarketError(f"eastmoney fastNewsList missing: {str(data)[:200]}")
        items: list[dict[str, Any]] = []
        for r in raw_list[:limit]:
            if not isinstance(r, dict):
                continue
            published = _bj_to_utc(str(r.get("showTime", "")))
            # stockList 元素有时是 dict（{code, name}）有时直接是字符串代码
            codes: list[str] = []
            for s in r.get("stockList") or []:
                if isinstance(s, dict) and s.get("code"):
                    codes.append(str(s["code"]))
                elif isinstance(s, str) and s:
                    codes.append(s)
            items.append(
                {
                    "title": str(r.get("title", "")).strip(),
                    "summary": str(r.get("summary", "")).strip(),
                    "published_at": published.isoformat() if published else None,
                    "related_codes": codes,
                }
            )
        self._cache_put(("news", limit), items)
        return items

    async def fetch_sector_board(self, top_n: int = 10) -> dict[str, Any]:
        """东财行业板块涨跌幅榜（一次请求拿全 ~500 板块，按涨跌幅排序取两端）。

        Returns: ``{total_boards, top: [...], bottom: [...]}``；单条含
        name / code / pct_chg(百分数) / up_count / down_count / leader / leader_pct_chg。
        """
        cached = self._cache_get(("sectors", top_n))
        if cached is not None:
            return cached

        async def _page(po: str) -> tuple[int, list[dict[str, Any]]]:
            # 东财 pz 单页上限 100，行业板块 ~500 个一页拿不全——
            # 涨幅端 po=1（降序）、跌幅端 po=0（升序）各取一页，总数读 data.total
            data = await self._get_json(
                "push2.eastmoney.com",
                _EM_BOARD_URL,
                params={
                    "pn": "1",
                    "pz": str(min(max(top_n, 1), 100)),
                    "po": po,
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",  # 按涨跌幅排序
                    "fs": "m:90+t:2",  # 90=板块市场, t:2=行业（t:3 是概念）
                    "fields": "f3,f12,f14,f104,f105,f128,f136,f140",
                },
            )
            payload = data.get("data") or {}
            diff = payload.get("diff")
            if not isinstance(diff, list) or not diff:
                raise CnMarketError(f"eastmoney board diff missing: {str(data)[:200]}")
            return _to_int(payload.get("total")) or len(diff), [
                _board_row(r) for r in diff if isinstance(r, dict)
            ]

        total, top = await _page("1")
        _, bottom = await _page("0")
        out = {"total_boards": total, "top": top, "bottom": bottom}
        self._cache_put(("sectors", top_n), out)
        return out

    async def fetch_moneyflow(self) -> dict[str, Any]:
        """同花顺沪深港通资金分钟流向（北向估算口径）。

        Returns: ``{as_of_time, hgt_net_yi_cny, sgt_net_yi_cny, north_net_yi_cny,
        series_sample, note}``；net 为当日累计净买入（亿元，负=净流出）。
        """
        cached = self._cache_get(("moneyflow",))
        if cached is not None:
            return cached
        data = await self._get_json("data.hexin.cn", _THS_HSGT_URL, params=None, headers=None)
        times = data.get("time")
        hgt = data.get("hgt")
        sgt = data.get("sgt")
        if not isinstance(times, list) or not isinstance(hgt, list) or not isinstance(sgt, list):
            raise CnMarketError(f"hexin dayChart shape unexpected: {str(data)[:200]}")

        def _last_valid(series: list[Any]) -> tuple[int, float] | None:
            for i in range(len(series) - 1, -1, -1):
                v = _to_float(series[i])
                if v is not None:
                    return i, v
            return None

        last_h = _last_valid(hgt)
        last_s = _last_valid(sgt)
        last_idx = max(
            (x[0] for x in (last_h, last_s) if x is not None),
            default=None,
        )
        # 每 ~30 分钟采一个点，给 agent 看日内节奏（全量 262 点对 LLM 是噪声）
        sample: list[dict[str, Any]] = []
        for i in range(0, len(times), 30):
            sample.append(
                {
                    "time": times[i],
                    "hgt": _to_float(hgt[i]) if i < len(hgt) else None,
                    "sgt": _to_float(sgt[i]) if i < len(sgt) else None,
                }
            )
        hgt_v = last_h[1] if last_h else None
        sgt_v = last_s[1] if last_s else None
        out = {
            "as_of_time": times[last_idx] if last_idx is not None and last_idx < len(times) else None,
            "hgt_net_yi_cny": hgt_v,
            "sgt_net_yi_cny": sgt_v,
            "north_net_yi_cny": (hgt_v + sgt_v) if hgt_v is not None and sgt_v is not None else None,
            "series_sample": sample,
            "note": NORTHBOUND_NOTE,
        }
        self._cache_put(("moneyflow",), out)
        return out

    async def fetch_strong_stocks(self, limit: int = 30) -> list[dict[str, Any]]:
        """同花顺当日强势股 + 人工题材标签（归因"什么主线在涨"的最直接证据）。

        Returns: ``[{code, name, reason, tags, date}]``；tags 为 reason 按 "+" 拆分。
        """
        cached = self._cache_get(("movers", limit))
        if cached is not None:
            return cached
        bj_today = datetime.now(_TZ_SHANGHAI).strftime("%Y-%m-%d")
        data = await self._get_json(
            "zx.10jqka.com.cn",
            _THS_HARDEN_URL.format(date=bj_today),
            params=None,
            headers=None,
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            raise CnMarketError(f"ths getharden shape unexpected: {str(data)[:200]}")
        items: list[dict[str, Any]] = []
        for r in rows[:limit]:
            reason = str(r.get("reason", "")).strip()
            items.append(
                {
                    "code": str(r.get("code", "")),
                    "name": str(r.get("name", "")),
                    "reason": reason,
                    "tags": [t.strip() for t in reason.split("+") if t.strip()],
                    "date": str(r.get("date", "")),
                }
            )
        self._cache_put(("movers", limit), items)
        return items

    # ── 治理与底层 ─────────────────────────────────────────────────

    async def _get_json(
        self,
        host_key: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """串行 + 限速 + 抖动的 GET。失败抛 ``CnMarketError``。"""
        lock = self._host_locks.setdefault(host_key, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._host_last.get(host_key, 0.0)
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0.1, 0.5))
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise CnMarketError(f"{host_key} request failed: {exc}") from exc
            finally:
                self._host_last[host_key] = time.monotonic()
        if resp.status_code in (403, 429):
            raise CnMarketError(f"{host_key} rate-limited/blocked: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise CnMarketError(f"{host_key} HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise CnMarketError(f"{host_key} returned non-JSON: {resp.text[:200]}") from exc
        if not isinstance(data, dict):
            raise CnMarketError(f"{host_key} returned non-object JSON")
        return data

    def _cache_get(self, key: tuple[Any, ...]) -> Any | None:
        if self._cache_ttl <= 0:
            return None
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires, value = hit
        if time.monotonic() > expires:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: tuple[Any, ...], value: Any) -> None:
        # 只缓存成功结果（失败一律上抛，不会走到这）；60s 挡 analyst fan-out 重复打源站
        if self._cache_ttl <= 0:
            return
        if len(self._cache) > 128:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + self._cache_ttl, value)

    async def close(self) -> None:
        await self._client.aclose()


def _board_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r.get("f14", ""),
        "code": r.get("f12", ""),
        "pct_chg": _to_float(r.get("f3")),
        "up_count": _to_int(r.get("f104")),
        "down_count": _to_int(r.get("f105")),
        "leader": r.get("f128", "") or "",
        "leader_code": r.get("f140", "") or "",
        "leader_pct_chg": _to_float(r.get("f136")),
    }


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    try:
        if v is None or v == "" or v == "-":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------- module-level singleton ----------

_connector: CnMarketConnector | None = None


def init_connector() -> CnMarketConnector:
    """启动时调一次。零 key。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("CnMarket connector already initialized")
    _connector = CnMarketConnector()
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    _connector = None


def get_connector() -> CnMarketConnector:
    if _connector is None:
        raise RuntimeError("CnMarket connector not initialized; call init_connector() first")
    return _connector

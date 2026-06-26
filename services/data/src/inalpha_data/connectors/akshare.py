"""akshare 全球股市 connector（A股 + 港股 + 日股 + 英股 + 德股）。

为什么用 akshare：

- 中港日英德主要市场全覆盖，零 API key
- 同步库（基于 requests + pandas），需 ``asyncio.to_thread`` 包装跑线程池
- 覆盖广但单源（聚合公开页），偶发反爬；MVP 阶段够用
- **韩 / 澳 / 印 / 巴西**等市场 akshare 没标准接口 → 走 ``yfinance`` connector 兜底

**symbol 格式约定**（venue=``"akshare"``）：

- A股沪市：``"sh.600519"``  → akshare ``stock_zh_a_hist`` symbol=``"600519"``
- A股深市：``"sz.000001"``  → 同上
- 港股   ：``"hk.00700"``  → akshare ``stock_hk_hist`` symbol=``"00700"``
- 日股   ：``"jp.6758"``    → akshare ``stock_jp_hist`` symbol=``"6758"``（索尼）
- 英股   ：``"uk.BARC"``    → akshare ``stock_uk_hist`` symbol=``"BARC"``（巴克莱）
- 德股   ：``"de.SAP"``     → akshare ``stock_de_hist`` symbol=``"SAP"``

**timeframe 支持**（MVP 限制）：

- ``"1d"`` / ``"1wk"`` / ``"1mo"``  → 直接传 ``period``
- 分钟级走 ``stock_zh_a_minute``（仅 A股），暂不实现，留 ``NotImplementedError``

历史窗口：akshare 默认拉 20 年起，足够做长期回测。
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "akshare"

#: fundamentals 进程内缓存 TTL（秒）。A股一次 fundamentals 要打 1 次财报摘要 + 3 次
#: Baidu 估值(串行 ~4-5s),research 多 analyst fan-out 会重复问同一标的;60s 缓存挡掉
#: 重复打源站,兼顾防封与延迟。基本面日级更新,60s 内复用不损时效。
_FIN_CACHE_TTL_S = 60.0

# akshare 的 ``period`` 字符串映射（仅日级及以上；分钟级要走另一个接口）
_PERIOD_MAP: dict[str, str] = {
    "1d": "daily",
    "1wk": "weekly",
    "1mo": "monthly",
}


#: 允许的市场前缀
_ALLOWED_PREFIXES = frozenset({"sh", "sz", "hk", "jp", "uk", "de"})


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """``"sh.600519"`` → ``("sh", "600519")``；``"jp.6758"`` → ``("jp", "6758")``。

    Raises:
        ValueError: 格式不符（缺 ``.`` 分隔 / prefix 不在允许集合）
    """
    if "." not in symbol:
        raise ValueError(
            f"akshare symbol must be '<prefix>.<code>'，prefix in (sh/sz/hk/jp/uk/de)，"
            f"got {symbol!r}"
        )
    prefix, code = symbol.split(".", 1)
    prefix = prefix.lower()
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError(
            f"akshare unknown prefix {prefix!r}，allow: {sorted(_ALLOWED_PREFIXES)}"
        )
    if not code:
        raise ValueError(f"akshare code is empty: {symbol!r}")
    return prefix, code


class AkshareConnector:
    """akshare 包装 —— 同步库走 ``asyncio.to_thread``。"""

    def __init__(self) -> None:
        # akshare 无 client 对象,import 即用。fundamentals 进程内 TTL 缓存,**PIT-aware**:
        # key = (symbol, as_of 截断到天 | None)。财报日级粒度,按天 key 安全——同一天的
        # PIT 查询(含 as_of≈now 的实时研究,三 analyst 并行)命中同一格,不再各自打 akshare
        # (#102 CR:原"PIT 绕过缓存"会让 live deep-dive 对同 ticker 打 3× 网络)。只缓存成功。
        self._fin_cache: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def _fin_cache_key(symbol: str, as_of: datetime | None) -> tuple[str, str | None]:
        return (symbol, as_of.date().isoformat() if as_of is not None else None)

    def _fin_cache_get(
        self, symbol: str, as_of: datetime | None = None
    ) -> dict[str, Any] | None:
        key = self._fin_cache_key(symbol, as_of)
        hit = self._fin_cache.get(key)
        if hit is None:
            return None
        expires, value = hit
        if time.monotonic() > expires:
            self._fin_cache.pop(key, None)
            return None
        return value

    def _fin_cache_put(
        self, symbol: str, value: dict[str, Any], as_of: datetime | None = None
    ) -> None:
        if len(self._fin_cache) > 128:  # 进程内软上限,超了清空(同 cn_market)
            self._fin_cache.clear()
        self._fin_cache[self._fin_cache_key(symbol, as_of)] = (
            time.monotonic() + _FIN_CACHE_TTL_S,
            value,
        )

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 akshare 拉 OHLCV。

        Args:
            symbol: ``"sh.600519"`` / ``"sz.000001"`` / ``"hk.00700"``
            timeframe: 仅支持 ``"1d"`` / ``"1wk"`` / ``"1mo"`` （MVP）
            since: UTC datetime；akshare 接 ``YYYYMMDD`` 字符串
            limit: 不直接生效（akshare 不接 limit，整段拉回；上层切片）

        Returns:
            list of ``(ts, open, high, low, close, volume)``，UTC aware。
        """
        if timeframe not in _PERIOD_MAP:
            raise NotImplementedError(
                f"akshare connector MVP only supports {sorted(_PERIOD_MAP)}; "
                f"intra-day not implemented yet"
            )

        prefix, code = _parse_symbol(symbol)
        period = _PERIOD_MAP[timeframe]
        start_str = since.strftime("%Y%m%d")
        # end 给 today 让 akshare 一口气拉全
        end_str = datetime.now(UTC).strftime("%Y%m%d")

        _logger.debug(
            "akshare_fetch_bars",
            symbol=symbol,
            timeframe=timeframe,
            since=since.isoformat(),
            start_str=start_str,
            limit=limit,
        )

        rows = await asyncio.to_thread(
            _fetch_sync,
            prefix=prefix,
            code=code,
            period=period,
            start_str=start_str,
            end_str=end_str,
        )

        # akshare 返的是 DataFrame；列名中文 / 英文都见过，做防御性归一化
        out: list[tuple[datetime, float, float, float, float, float]] = []
        for r in rows:
            ts_raw = r.get("日期") or r.get("date") or r.get("Date")
            o = _to_float(r.get("开盘") or r.get("open"))
            h = _to_float(r.get("最高") or r.get("high"))
            low = _to_float(r.get("最低") or r.get("low"))
            c = _to_float(r.get("收盘") or r.get("close"))
            v = _to_float(r.get("成交量") or r.get("volume"))
            if ts_raw is None or o is None or c is None:
                # 列名都没识别出来 → 配置问题，跳过避免静默写脏数据
                continue
            ts = _parse_date(ts_raw)
            out.append((ts, o or 0.0, h or 0.0, low or 0.0, c, v or 0.0))

        # 按 limit 截断尾部（akshare 不接 limit，整段返）
        if limit and len(out) > limit:
            out = out[-limit:]
        return out

    async def fetch_financials(
        self, symbol: str, as_of: str | None = None
    ) -> dict[str, Any]:
        """拉 A股 / 港股 财报基本面数据。

        A-share: ``ak.stock_financial_abstract(symbol=code)``
        HK stock: ``ak.stock_hk_financial_abstract(symbol=code)``

        akshare 返回字段因市场不同有差异，做防御性字段映射；缺失字段置 None 不抛异常。

        ``as_of``（ISO 时间串，ADR-0053 阶段 A）：point-in-time 截断——只取"报告期末 +
        发布滞后 <= as_of"的财报期，防回测看到当时还没披露的财报（未来函数）。缓存为
        **PIT-aware**：按 ``(symbol, as_of 截断到天)`` 分格，同一天的 PIT 查询复用、非 PIT
        (None) 自成一格（#102 CR）。

        **限制（A股估值字段无 PIT）**：``sh/sz`` 的 ``market_cap/pe_ratio/pb_ratio`` 来自 Baidu
        **实时**源，无历史回溯。故**历史 as_of**（早于今天）查询时**跳过估值**（留空），避免把当日
        估值混进历史财报造成时序错配（未来函数）；``as_of=今天/None`` 的实时研究照常取（即时正确）。
        """
        prefix, code = _parse_symbol(symbol)
        if prefix not in ("sh", "sz", "hk"):
            return {
                "venue": VENUE,
                "symbol": symbol,
                "available": False,
                "reason": f"financials not supported for akshare prefix {prefix!r}",
            }

        as_of_dt: datetime | None = None
        if as_of is not None:
            try:
                as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                if as_of_dt.tzinfo is None:
                    as_of_dt = as_of_dt.replace(tzinfo=UTC)
                # 归一到 UTC：否则带非 UTC offset(+08:00)时 .date() 是本地日期,与
                # now(UTC).date() 比 / 缓存按天分格都会错位(#102 CR)。
                as_of_dt = as_of_dt.astimezone(UTC)
            except ValueError:
                return {
                    "venue": VENUE,
                    "symbol": symbol,
                    "available": False,
                    "reason": f"invalid as_of datetime: {as_of!r}",
                }

        # PIT-aware 缓存：按 (symbol, as_of 截断到天) 命中——非 PIT(None)与各 as_of 各自一格，
        # 同一天的 PIT 查询(含实时研究)复用，不再绕过缓存（#102 CR）。
        cached = self._fin_cache_get(symbol, as_of_dt)
        if cached is not None:
            return cached

        _logger.debug(
            "akshare_fetch_financials", symbol=symbol, prefix=prefix, code=code, as_of=as_of
        )

        try:
            raw = await asyncio.to_thread(
                _fetch_financials_sync,
                prefix=prefix,
                code=code,
                as_of=as_of_dt,
                publish_lag_days=FINANCIALS_PUBLISH_LAG_DAYS,
            )
        except Exception as exc:
            _logger.warning("akshare_financials_fetch_failed", symbol=symbol, error=str(exc))
            return {
                "venue": VENUE,
                "symbol": symbol,
                "available": False,
                "reason": f"akshare fetch failed: {exc}",
            }

        if raw is None or (isinstance(raw, (dict, list)) and len(raw) == 0):
            reason = (
                f"no financials published as of {as_of}"
                if as_of_dt is not None
                else "akshare returned empty financial data"
            )
            return {
                "venue": VENUE,
                "symbol": symbol,
                "available": False,
                "reason": reason,
            }

        from datetime import datetime as dt_dt

        indicators: dict[str, float | None] = {}
        # raw 由 _flatten_abstract 拍平成 {指标名: 最新期值}。
        # 键名以 stock_financial_abstract 实际「指标」列为准（2026-06 实测）；
        # 旧的简写键保留作其他市场 / 版本 / 未来估值源的前向兼容（命中即用，命不中无害）。
        _indicator_map = {
            # 盈利
            "净资产收益率(ROE)": "roe",
            "净资产收益率": "roe",
            "毛利率": "gross_margin",
            "销售净利率": "net_margin",
            "净利率": "net_margin",
            # 成长
            "营业总收入增长率": "revenue_yoy",
            "营业收入同比增长": "revenue_yoy",
            "归属母公司净利润增长率": "profit_yoy",
            "归属净利润同比增长": "profit_yoy",
            # 杠杆（A股摘要给的是资产负债率；近似填入 leverage 槽，前端按杠杆指标展示）
            "资产负债率": "debt_to_equity",
            # 估值：stock_financial_abstract 不含，A股另走 Baidu 源补齐(见下方)。
            # 以下键留作前向兼容(摘要/其他市场若带了即用,命不中无害)。
            "总市值": "market_cap",
            "流通市值": "market_cap",
            "市盈率": "pe_ratio",
            "市净率": "pb_ratio",
            # 财务质量项（供应链瓶颈调研的红旗检查：存货应收增速 vs 收入、现金流）
            # akshare 摘要表字段随版本/市场浮动，防御性映射：缺了置 None 不报错
            "经营现金流量净额": "operating_cashflow",
            "每股经营现金流": "ocf_per_share",
            "存货周转率": "inventory_turnover",
            "存货周转天数": "inventory_days",
            "应收账款周转率": "receivables_turnover",
            "应收账款周转天数": "receivables_days",
            "流动比率": "current_ratio",
            "速动比率": "quick_ratio",
        }
        for cn_key, en_key in _indicator_map.items():
            val = raw.get(cn_key)
            if val is not None:
                try:
                    indicators[en_key] = float(val)
                except (TypeError, ValueError):
                    pass

        # 单位归一:akshare 摘要的 ROE / 利润率 / 增长率均为**百分数**(如 18.8 表示
        # 18.8%);yfinance 同名字段是**分数**(0.188)。统一成分数对齐 yfinance,
        # 前端按分数 ×100 展示。(debt_to_equity / 估值字段不在此列,保持原值。)
        for _pct_key in ("roe", "gross_margin", "net_margin", "revenue_yoy", "profit_yoy"):
            v = indicators.get(_pct_key)
            if v is not None:
                indicators[_pct_key] = v / 100.0

        # 估值(总市值/PE/PB)不在财报摘要里 → A股另走 Baidu 源补齐(best-effort,
        # 失败只记日志不阻断已拿到的盈利/成长/财务指标)。
        # **PIT 守门(#102 CR)**：Baidu 估值是**实时**源、无历史回溯。历史 as_of(早于今天)查询
        # 时**跳过**——否则把当日估值混进 PIT 过滤后的历史财报 = 时序错配/未来函数(2020 的 ROE
        # 配当日 PE 会让相对估值反向)。as_of=今天/None 的实时研究照取(即时正确)。
        _is_historical = as_of_dt is not None and as_of_dt.date() < dt_dt.now(tz=UTC).date()
        if (
            prefix in ("sh", "sz")
            and not _is_historical
            and not all(
                indicators.get(k) is not None
                for k in ("market_cap", "pe_ratio", "pb_ratio")
            )
        ):
            try:
                valuation = await asyncio.to_thread(_fetch_valuation_sync, code)
                for k, v in valuation.items():
                    if indicators.get(k) is None:
                        indicators[k] = v
            except Exception as exc:
                _logger.warning("akshare_valuation_fetch_failed", symbol=symbol, error=str(exc))

        # as_of 回填：PIT 查询回显请求的 as_of（数据有效时点）；非 PIT 回填取数时刻
        as_of_echo = (
            as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if as_of_dt is not None
            else dt_dt.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        result = {
            "venue": VENUE,
            "symbol": symbol,
            "available": True,
            "as_of": as_of_echo,
            "indicators": indicators,
            "raw": raw,
        }
        # 缓存成功结果（PIT-aware，按 (symbol, as_of 天) 分格）；失败路径不缓存,留待重试
        self._fin_cache_put(symbol, result, as_of_dt)
        return result

    async def fetch_news(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        """拉 A股 个股新闻（东方财富来源，零 key）。

        Args:
            symbol: ``"sh.600519"`` / ``"sz.000001"``（仅 A股支持新闻；港股无公开新闻接口）
            limit: 最多返回多少条

        Returns:
            list of dict，每条含 ``{title, publisher, link, published_at, summary}``；
            空列表表示当天无新闻或 symbol 不在 sh/sz。
        """
        prefix, code = _parse_symbol(symbol)
        if prefix not in ("sh", "sz"):
            _logger.debug("akshare_fetch_news_unsupported_prefix", symbol=symbol, prefix=prefix)
            return []

        _logger.debug("akshare_fetch_news", symbol=symbol, prefix=prefix, code=code, limit=limit)

        try:
            raw = await asyncio.to_thread(_fetch_news_sync, symbol=code)
        except Exception as exc:
            _logger.warning("akshare_news_fetch_failed", symbol=symbol, error=str(exc))
            return []

        if not raw or not isinstance(raw, list):
            return []


        out: list[dict[str, Any]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("标题") or ""
            if not title:
                continue
            # 东方财富新闻时间格式：'2025-05-21 10:30:00' 或时间戳
            ts_raw = item.get("time") or item.get("发布时间") or item.get("datetime")
            published_at: str | None = None
            if ts_raw:
                try:
                    if isinstance(ts_raw, (int, float)):
                        from datetime import datetime as dt_dt_dt
                        published_at = dt_dt_dt.fromtimestamp(int(ts_raw), tz=UTC).isoformat()
                    else:
                        from datetime import datetime as dt_dt_dt
                        published_at = dt_dt_dt.strptime(str(ts_raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
                except (ValueError, OSError):
                    published_at = None

            out.append({
                "title": title,
                "publisher": item.get("source") or item.get("来源") or "",
                "link": item.get("url") or item.get("链接") or "",
                "published_at": published_at,
                "summary": (item.get("content") or item.get("内容") or "")[:500],
            })
        return out

    async def fetch_index_constituents(self, index_code: str) -> list[dict[str, Any]]:
        """拉指数**当前**成分（中证指数网，零 key）。

        Args:
            index_code: 中证/常用指数代码，如 ``"000300"``(沪深300) / ``"000905"``(中证500)。

        Returns:
            ``[{code, name, weight}]``——code 归一为 sh./sz./bj. 前缀的 Inalpha 符号格式;
            name/weight 可空。空 list = 拉取失败 / 不支持(优雅降级)。

        坑:akshare 只回**当前**成分(中证 XLS 每日覆盖、无 as-of 历史);**PIT 历史靠本服务
        每日快照向前累积**(#106 / ADR-0053 阶段 C),本方法只提供"今天的成分"这一原料。
        """
        _logger.debug("akshare_fetch_constituents", index_code=index_code)
        try:
            rows = await asyncio.to_thread(_fetch_constituents_sync, index_code=index_code)
        except Exception as exc:
            _logger.warning(
                "akshare_constituents_fetch_failed", index_code=index_code, error=str(exc)
            )
            return []
        return rows

    async def close(self) -> None:
        # akshare 无连接对象需要关
        return None


#: 财报发布滞后保守估计（ADR-0053 阶段 A · point-in-time）：报告期末 + 此天数 ≈ 法定披露
#: 上限（A股年报次年 4/30 ≈ 120 天；季报法定更早，取 120 天对"as_of 时是否已发布"的判定
#: 偏保守——宁可晚一期也不偷看未发布财报。拿不到真实公告日时的兜底口径（ADR-0053 OQ1）。
FINANCIALS_PUBLISH_LAG_DAYS = 120


def _period_publishable(period: str, as_of: datetime, lag_days: int) -> bool:
    """报告期(YYYYMMDD)在 ``as_of`` 时是否已发布：报告期末 + lag_days <= as_of。"""
    try:
        end = datetime.strptime(str(period), "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return False
    return end + timedelta(days=lag_days) <= as_of


def _fetch_financials_sync(
    *,
    prefix: str,
    code: str,
    as_of: datetime | None = None,
    publish_lag_days: int = FINANCIALS_PUBLISH_LAG_DAYS,
) -> dict[str, Any]:
    """同步调 akshare 财报接口 —— 按前缀路由,返回 ``{指标名: 最新期值}``。

    ``stock_financial_abstract`` / ``stock_hk_financial_abstract`` 返回的是
    「指标 × 报告期日期列」的**转置表**(行=指标名,列=各报告期,如 20260331)。
    旧实现 ``raw.iloc[-1].to_dict()`` 只取了**最后一行**(单个指标)→ 上层
    ``raw.get("净资产收益率")`` 全落空、indicators 恒为 null。这里改为遍历整表、
    每个指标取最新一期的**非空**值,拍平成 {指标名: 值} 供上层按名映射。
    """
    import akshare as ak

    if prefix in ("sh", "sz"):
        raw = ak.stock_financial_abstract(symbol=code)
    else:
        raw = ak.stock_hk_financial_abstract(symbol=code)

    return _flatten_financial_abstract(raw, as_of=as_of, publish_lag_days=publish_lag_days)


def _flatten_financial_abstract(
    raw: Any,
    *,
    as_of: datetime | None = None,
    publish_lag_days: int = FINANCIALS_PUBLISH_LAG_DAYS,
) -> dict[str, Any]:
    """转置财报表 → ``{指标名: 最新非空值}``;结构不符时退化兜底,绝不抛错。

    ``as_of`` 非空时只保留"报告期末 + 发布滞后 <= as_of"的报告期列（PIT 防未来函数：
    回测在 as_of 时刻不应看到当时还没披露的财报）；过滤后无可用期 → 返回 ``{}``。
    """
    import math

    if raw is None:
        return {}
    if not hasattr(raw, "columns"):
        return raw if isinstance(raw, dict) else {}

    cols = [str(c) for c in raw.columns]
    # 8 位数字列即报告期(20260331);新→旧排序,优先取最近一期。
    date_cols = sorted(
        (c for c in raw.columns if str(c).isdigit() and len(str(c)) == 8),
        key=lambda c: str(c),
        reverse=True,
    )
    # PIT 截断：剔除 as_of 时尚未发布的报告期（ADR-0053 阶段 A）
    if as_of is not None:
        date_cols = [c for c in date_cols if _period_publishable(str(c), as_of, publish_lag_days)]
        if not date_cols:
            return {}
    if "指标" not in cols or not date_cols:
        # 不是预期的转置表 → 退回最后一行(旧行为)兜底,至少不丢数据。
        # 但 PIT 模式下**绝不走 iloc[-1] 后门**：它返回全部列(含未披露报告期),会让
        # 格式异常时偷看未来财报、PIT 约束失效(#100 CR)。宁可返空也不泄漏。
        if as_of is not None:
            return {}
        if len(raw) == 0:
            return {}
        row = raw.iloc[-1]
        return row.to_dict() if hasattr(row, "to_dict") else dict(row)

    out: dict[str, Any] = {}
    for _, row in raw.iterrows():
        name = row.get("指标")
        if name is None:
            continue
        for dc in date_cols:
            val = row.get(dc)
            if val is None:
                continue
            if isinstance(val, float) and math.isnan(val):
                continue
            out[str(name)] = val
            break  # 该指标已取到最新非空值
    return out


def _fetch_valuation_sync(code: str) -> dict[str, float]:
    """A股估值(总市值 / 市盈率TTM / 市净率)—— 走 Baidu 源
    (``stock_zh_valuation_baidu``),非 eastmoney、不被本地代理拦
    (financial_abstract 不含估值;eastmoney 的 stock_individual_info_em 被代理拦)。

    每个指标一次调用,串行 + 小睡防封;单项失败跳过不影响其余与基本面。
    Baidu 总市值单位为**亿元**,×1e8 转绝对值对齐 yfinance(前端 fmtCap 按亿/万亿展示)。
    """
    import math
    import time as _time

    import akshare as ak

    out: dict[str, float] = {}
    plan = [
        ("总市值", "market_cap", 1e8),
        ("市盈率(TTM)", "pe_ratio", 1.0),
        ("市净率", "pb_ratio", 1.0),
    ]
    for i, (indicator, key, scale) in enumerate(plan):
        if i:
            _time.sleep(0.5)  # 防封:同源连续调用留间隔
        try:
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近一年")
            if df is None or not hasattr(df, "iloc") or len(df) == 0:
                continue
            val = df.iloc[-1].get("value")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            out[key] = float(val) * scale
        except Exception:
            continue  # 单项失败跳过,不阻断其余估值/基本面
    return out


def _fetch_news_sync(symbol: str) -> list[dict[str, Any]]:
    """同步调 akshare 新闻接口 —— ``stock_news_em``（东方财富 A股新闻）。

    返回 list of dict，字段含：标题 / 发布时间 / 来源 / 链接 / 内容 等。
    """
    import akshare as ak

    raw = ak.stock_news_em(symbol=symbol)
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        return []
    if hasattr(raw, "to_dict"):
        return raw.to_dict(orient="records")  # type: ignore[no-any-return]
    if isinstance(raw, list):
        return raw
    return []


def _fetch_sync(
    *,
    prefix: str,
    code: str,
    period: str,
    start_str: str,
    end_str: str,
) -> list[dict[str, Any]]:
    """同步调 akshare —— 按市场前缀路由到对应函数。

    单独抽函数让 ``asyncio.to_thread`` 序列化参数更明确，也方便测试 monkeypatch。

    支持的 akshare 入口：

    - ``stock_zh_a_hist``：A股（sh/sz）
    - ``stock_hk_hist``  ：港股（hk）
    - ``stock_jp_hist``  ：日股（jp）
    - ``stock_uk_hist``  ：英股（uk）
    - ``stock_de_hist``  ：德股（de）
    """
    import akshare as ak

    common = dict(
        symbol=code,
        period=period,
        start_date=start_str,
        end_date=end_str,
    )

    if prefix in ("sh", "sz"):
        # A股 daily / weekly / monthly；带 adjust 参数
        df = ak.stock_zh_a_hist(adjust="", **common)
    elif prefix == "hk":
        df = ak.stock_hk_hist(adjust="", **common)
    elif prefix == "jp":
        # akshare 0.13+：stock_jp_hist 不接 adjust 参数
        df = ak.stock_jp_hist(**common)
    elif prefix == "uk":
        df = ak.stock_uk_hist(**common)
    elif prefix == "de":
        df = ak.stock_de_hist(**common)
    else:
        raise ValueError(f"unreachable: prefix {prefix!r} should be filtered earlier")

    if df is None or len(df) == 0:
        return []
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


def _cn_symbol(raw_code: str) -> str:
    """6 位 A股代码 → Inalpha 符号（sh./sz./bj. 前缀，与 _parse_symbol 对称）。

    6/9→沪(主板/B)、0/2/3→深(主板·中小/B/创业)、4/8→北交所;其它兜底沪。
    """
    c = raw_code.strip().split(".")[-1]  # 已带前缀时取纯数字部分
    if c[:1] in ("6", "9"):
        return f"sh.{c}"
    if c[:1] in ("4", "8"):
        return f"bj.{c}"
    return f"sz.{c}"


def _fetch_constituents_sync(*, index_code: str) -> list[dict[str, Any]]:
    """同步调 akshare 取指数**当前**成分（带权重优先，回退无权重接口）。

    akshare 列名随接口/版本变动 → 用模糊匹配抽 代码/名称/权重，不硬钉列名。
    """
    import akshare as ak

    df = None
    try:
        df = ak.index_stock_cons_weight_csindex(symbol=index_code)
    except Exception:
        df = None
    if df is None or len(df) == 0:
        df = ak.index_stock_cons_csindex(symbol=index_code)
    if df is None or len(df) == 0:
        return []

    cols = [str(c) for c in df.columns]

    def _find(*keys: str) -> str | None:
        for c in cols:
            if any(k in c for k in keys):
                return c
        return None

    code_col = _find("成分券代码", "证券代码", "股票代码", "代码")
    name_col = _find("成分券名称", "证券名称", "股票名称", "简称", "名称")
    weight_col = _find("权重")
    if code_col is None:
        return []

    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw = row.get(code_col)
        if raw is None:
            continue
        code = str(raw).strip().zfill(6)
        if not code.isdigit() or len(code) != 6:
            continue
        out.append(
            {
                "code": _cn_symbol(code),
                "name": str(row.get(name_col)).strip() if name_col else None,
                "weight": _to_float(row.get(weight_col)) if weight_col else None,
            }
        )
    return out


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v: Any) -> datetime:
    """akshare 日期是 ``datetime.date`` / ``str`` / ``pd.Timestamp``，统一成 UTC aware。"""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    # pd.Timestamp 兼容
    if hasattr(v, "to_pydatetime"):
        dt = v.to_pydatetime()
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    # date 转 datetime
    if hasattr(v, "year") and not hasattr(v, "hour"):
        return datetime(v.year, v.month, v.day, tzinfo=UTC)
    # 字符串 "2025-05-21"
    return datetime.fromisoformat(str(v)).replace(tzinfo=UTC)


# ---------- module-level singleton ----------

_connector: AkshareConnector | None = None


def init_connector() -> AkshareConnector:
    """启动时调一次。akshare 无 API key 需要。"""
    global _connector
    if _connector is not None:
        raise RuntimeError("Akshare connector already initialized")
    _connector = AkshareConnector()
    register_connector(VENUE, _connector)
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    unregister_connector(VENUE)
    _connector = None


def get_connector() -> AkshareConnector:
    if _connector is None:
        raise RuntimeError("Akshare connector not initialized; call init_connector() first")
    return _connector

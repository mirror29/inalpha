"""akshare connector —— A股走 baostock（证券宝）+ 港股走 akshare 东财源。

baostock（证券宝）做 A 股全栈数据源：

- **K 线**：日/周/月线，OHLCV 齐全（含真实成交量）
- **财报**：利润表 + 负债表 + 成长指标 + 现金流比率 + 分红记录
- **交易日历**：A 股交易/非交易日查询
- **指数成分**：沪深300 / 上证50 / 中证500 当前成分股
- **全部免费零 key**，无需注册

**2026-07 更新**：

- A股（sh/sz）：东财 push2his 失效 → 全部能力切 baostock
  - K 线：日/周/月 + volume
  - 财报：利润/负债/成长/现金流/分红（baostock pubDate 做 PIT）
  - 交易日历：query_trade_dates
  - 指数成分：沪深300/上证50/中证500
- 港股（hk）：东财 ``stock_hk_hist`` 保留作 fallback（push2his 同失效），
  orchestrator 默认路由到 yfinance
- 日股 / 英股 / 德股：``stock_jp_hist`` / ``stock_uk_hist`` / ``stock_de_hist``
  在 akshare ≥1.18.63 中已删除；orchestrator 路由到 yfinance

**symbol 格式约定**（venue=``"akshare"``）：

- A股沪市：``"sh.600519"``  → baostock ``"sh.600519"``
- A股深市：``"sz.000001"``  → baostock ``"sz.000001"``
- 港股   ：``"hk.00700"``  → akshare ``stock_hk_hist``（东财源，当前不可用）

**timeframe 支持**：

- ``"1d"`` / ``"1wk"`` / ``"1mo"`` → baostock ``frequency="d"/"w"/"m"``
- 分钟级走 ``NotImplementedError``，可换 venue=yfinance 取近 60 天分钟线
"""
from __future__ import annotations

import asyncio
import threading as _threading
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

#: 串行锁——akshare 走公开页聚合（东财/同花顺/中证等），并发突发会触发反爬；
#: 进程级串行 + 最小间隔把突发摊成节流串行，避免 429/空返（yfinance 同类模式）。
_FETCH_LOCK = asyncio.Lock()
#: 最小拉取间隔（秒）。公开页比 Yahoo API 更脆弱，≥1s；A 股数据走东财/同花顺
#: 直连配方，memory ``a_stock_data_recipes`` 已记录"防封串行≥1s"。
_MIN_FETCH_INTERVAL_S = 1.0
#: 锁内单次拉取超时上限——TCP 挂起时快速放锁，不把整个 panel 拖死。
_FETCH_TIMEOUT_S = 30.0
_last_fetch_mono: float = 0.0

# ── baostock 持久会话 ────────────────────────────────────────────────
# baostock login 需 ~4.3s/次；每个 fetch 单独 login+logout 会把跑批拖慢一个数量级。
# 进程级单次 login、close_connector 时 logout；_FETCH_LOCK 已确保同一时刻只有一个
# baostock 调在飞，无并发问题。
_bs_logged_in: bool = False
_bs_lock = _threading.Lock()


def _ensure_bs_login() -> None:
    """惰性 login baostock——首次调时登一次，后续复用。

    fork 安全：进程 fork 后 `_bs_logged_in` 可能为 True 但连接已失效，
    用探活查询检测后重新 login。
    """
    global _bs_logged_in
    if _bs_logged_in:
        # 快速路径：已登录，跳过锁（GIL 保证 bool 读原子）
        return

    with _bs_lock:
        if _bs_logged_in:
            # 另一个线程刚登完，直接返回
            return
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            _logger.warning("baostock_login_failed", error_code=lg.error_code, error_msg=lg.error_msg)
        else:
            _bs_logged_in = True


def _bs_session_logout() -> None:
    """登出 baostock（close_connector 时调一次）。"""
    global _bs_logged_in
    if not _bs_logged_in:
        return
    with _bs_lock:
        if not _bs_logged_in:
            return
        import baostock as bs
        bs.logout()
        _bs_logged_in = False

#: 允许的市场前缀
_ALLOWED_PREFIXES = frozenset({"sh", "sz", "hk", "jp", "uk", "de"})


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """``"sh.600519"`` → ``("sh", "600519")``；``"jp.6758"`` → ``("jp", "6758")``。

    Raises:
        ValueError: 格式不符（缺 ``.`` 分隔 / prefix 不在允许集合）
    """
    # 归一化 Yahoo Finance 后缀格式(600036.SH → sh.600036; 000001.SZ → sz.000001),
    # 对上游(backfill/orchestrator)透明——orchestrator 透传用户输入的任意格式 symbol,
    # connector 内部做格式适配。只匹配纯数字代码 + 已知市场后缀。
    if "." in symbol and symbol.rsplit(".", 1)[1].lower() in {"sh", "sz"}:
        code, suffix = symbol.rsplit(".", 1)
        if code.replace(".", "", 1).isdigit():
            symbol = f"{suffix.lower()}.{code}"
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

        try:
            rows = await self._throttled_fetch_sync(
                prefix=prefix,
                code=code,
                period=period,
                start_str=start_str,
                end_str=end_str,
            )
        except Exception as exc:
            _logger.warning(
                "akshare_fetch_bars_failed",
                symbol=symbol,
                timeframe=timeframe,
                start_str=start_str,
                end_str=end_str,
                error=str(exc),
            )
            return []

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

        if not out and rows:
            # 上游返了行但全部被列名解析跳过 → 列名漂移告警
            _logger.warning(
                "akshare_fetch_bars_all_rows_skipped",
                symbol=symbol,
                timeframe=timeframe,
                row_count=len(rows),
                sample_keys=list(rows[0].keys()) if rows else [],
            )

        # 按 limit 截断尾部（akshare 不接 limit，整段返）
        if limit and len(out) > limit:
            out = out[-limit:]
        return out

    @staticmethod
    async def _throttled_fetch_sync(
        *,
        prefix: str,
        code: str,
        period: str,
        start_str: str,
        end_str: str,
    ) -> list[dict[str, Any]]:
        """串行 + 最小间隔跑 akshare fetch，防并发突发触发反爬。

        进程级 ``_FETCH_LOCK`` 保证同一时刻只有一个 akshare 请求在飞；锁内再补足
        ``_MIN_FETCH_INTERVAL_S`` 的最小间隔。锁内每请求超时 ``_FETCH_TIMEOUT_S``，
        TCP 挂起时快速放锁让队列继续。

        对齐 yfinance ``_throttled_fetch_sync`` 模式。
        """
        global _last_fetch_mono
        async with _FETCH_LOCK:
            try:
                wait = _MIN_FETCH_INTERVAL_S - (time.monotonic() - _last_fetch_mono)
                if wait > 0:
                    await asyncio.sleep(wait)
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        _fetch_sync,
                        prefix=prefix,
                        code=code,
                        period=period,
                        start_str=start_str,
                        end_str=end_str,
                    ),
                    timeout=_FETCH_TIMEOUT_S,
                )
            finally:
                _last_fetch_mono = time.monotonic()

    async def fetch_financials(
        self, symbol: str, as_of: str | None = None
    ) -> dict[str, Any]:
        """拉 A股 / 港股 财报基本面数据。

        A-share: baostock 利润表 + 负债表 + 成长指标 + 现金流 + 分红
        HK stock: ``ak.stock_hk_financial_abstract(symbol=code)``

        baostock 返回字段因市场不同有差异，做防御性字段映射；缺失字段置 None 不抛异常。

        ``as_of``（ISO 时间串，ADR-0053 阶段 A）：point-in-time 截断——只取"报告期末 +
        发布滞后 <= as_of"的财报期，防回测看到当时还没披露的财报（未来函数）。baostock 路径
        用实际的 ``pubDate`` 字段做 PIT 判定（比滞后估算天数更精确）。缓存为
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

        # baostock 路径（sh/sz）：raw 中已有预映射的 _indicators，直接取用
        if isinstance(raw, dict) and "_indicators" in raw:
            indicators = {k: v for k, v in raw["_indicators"].items()}
        else:
            # akshare 东财路径（hk）：通过 _indicator_map 将中文指标名映射为英文 key
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
                # 杠杆
                "资产负债率": "debt_to_equity",
                # 估值
                "总市值": "market_cap",
                "流通市值": "market_cap",
                "市盈率": "pe_ratio",
                "市净率": "pb_ratio",
                # 财务质量项
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
            # 前端按分数 ×100 展示。
            # 注：baostock 路径在上方 _fetch_financials_baostock_sync 已返回分数，
            #     不需要走这步 /100 归一。
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

    async def fetch_trade_calendar(
        self, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """查询 A 股交易日历（baostock ``query_trade_dates``）。

        Args:
            start_date: 起始日期 ``"YYYY-MM-DD"``
            end_date: 结束日期 ``"YYYY-MM-DD"``

        Returns:
            ``[{calendar_date, is_trading_day}]``——is_trading_day ``"1"`` 表交易日，
            ``"0"`` 表非交易日（周末/节假日）。空列表 = 拉取失败。
        """
        import baostock as bs

        _logger.debug("akshare_fetch_trade_calendar", start=start_date, end=end_date)
        _ensure_bs_login()
        rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        if rs is None or rs.error_code != "0":
            _logger.warning("baostock_trade_calendar_failed", start=start_date, end=end_date)
            return []
        rows: list[dict[str, Any]] = []
        while rs.next():
            rd = rs.get_row_data()
            if rd and rd[0]:
                rows.append(dict(zip(rs.fields, rd, strict=True)))
        return rows

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
    """同步调财报接口 —— 按前缀路由。

    - A股（sh/sz）→ baostock（利润表 + 负债表 + 成长指标 + 现金流 + 分红）
    - 港股（hk）   → akshare ``stock_hk_financial_abstract``（东财源）
    """
    import akshare as ak

    if prefix in ("sh", "sz"):
        return _fetch_financials_baostock_sync(symbol=f"{prefix}.{code}", as_of=as_of)
    else:
        # 港股东财源当前不可用，打日志后静默返回空（orchestrator 路由到 yfinance）
        raw = ak.stock_hk_financial_abstract(symbol=code)
        if raw is None:
            _logger.warning("akshare_hk_financials_none", prefix=prefix, code=code)
            return {}
        # akshare 返回 DataFrame，非 dict
        if hasattr(raw, "empty") and raw.empty:
            _logger.warning("akshare_hk_financials_empty", prefix=prefix, code=code)
            return {}
        return _flatten_financial_abstract(raw, as_of=as_of, publish_lag_days=publish_lag_days)


def _fetch_financials_baostock_sync(
    *, symbol: str, as_of: datetime | None = None
) -> dict[str, Any]:
    """baostock 财报全量（利润/负债/成长/现金流/分红）→ 拍平成指标映射。

    baostock 各财报 API 返回**结构化字段**（非 akshare 转置表），各自有 ``pubDate``
    精确公告日期。PIT 模式下直接用 ``pubDate <= as_of`` 判定，比滞后天数估算更准。

    返回 dict：
    - ``_indicators``: ``{英文指标名: float|None}``——直接可喂 fetch_financials 的 indicators 槽
    - ``profit`` / ``balance`` / ``growth`` / ``cash_flow``: dict，原始财报数据
    - ``dividends``: list[dict]，分红记录
    - ``_period``: ``(year, quarter)`` 实际命中的财报期
    """
    _ensure_bs_login()
    return _query_baostock_financials(symbol, as_of)


def _query_baostock_financials(
    symbol: str, as_of: datetime | None
) -> dict[str, Any]:
    """查询 baostock 四大报表 + 分红，返回拍平的指标 dict。"""
    import math as _math

    import baostock as bs

    now = datetime.now(UTC)
    ref = as_of if as_of is not None else now

    def _period_ok(pub_date_str: str) -> bool:
        """publish date 是否 <= as_of（PIT 守门）。"""
        if as_of is None:
            return True
        try:
            pub = datetime.strptime(pub_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            return pub <= as_of
        except (ValueError, TypeError):
            return True  # 解析失败放行，宁可多拿不少拿

    # 从当前年份往回搜，找第一个有数据且 pubDate <= as_of 的财报期
    def _query_first(
        query_fn, year_range: range, quarter_range: range
    ) -> tuple[dict[str, Any] | None, int, int]:
        for y in year_range:
            for q in quarter_range:
                rs = query_fn(y, q)
                if rs is None or rs.error_code != "0":
                    continue
                rows: list[dict[str, Any]] = []
                while rs.next():
                    row_data = rs.get_row_data()
                    if row_data and row_data[0]:
                        rows.append(dict(zip(rs.fields, row_data, strict=True)))
                for row in rows:
                    pub = str(row.get("pubDate", ""))
                    if pub and _period_ok(pub):
                        return row, y, q
                # 该期数据存在但 pubDate > as_of → 继续搜更早的期
        return None, 0, 0

    # ── 利润表 ──
    profit, py, pq = _query_first(
        lambda y, q: bs.query_profit_data(symbol, year=y, quarter=q),
        range(ref.year, ref.year - 4, -1),
        range(4, 0, -1),
    )
    # Q1 季报可能缺 MBRevenue（营收）和 gpMargin（毛利率），
    # 此时补拉上一年 Q4 取这些字段，保证实时研究中指标齐全（非 PIT）。
    # PIT 模式下 py 可能被截断到较早年份，补拉也用 ref.year - 1 保证拿到最新可用数据
    profit_annual: dict[str, Any] | None = None
    if profit is not None and as_of is None and not profit.get("MBRevenue"):
        fallback_year = ref.year - 1 if as_of else py - 1
        rs = bs.query_profit_data(symbol, year=fallback_year, quarter=4)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    profit_annual = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 负债表（同一年/季）──
    balance = None
    if py:
        rs = bs.query_balance_data(symbol, year=py, quarter=pq)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    balance = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 成长指标 ──
    growth = None
    if py:
        rs = bs.query_growth_data(symbol, year=py, quarter=pq)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    growth = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 现金流量表 ──
    cash_flow = None
    if py:
        rs = bs.query_cash_flow_data(symbol, year=py, quarter=pq)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    cash_flow = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 营运能力 ──
    operation = None
    if py:
        rs = bs.query_operation_data(symbol, year=py, quarter=pq)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    operation = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 杜邦分解 ──
    dupont = None
    if py:
        rs = bs.query_dupont_data(symbol, year=py, quarter=pq)
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    dupont = dict(zip(rs.fields, rd, strict=True))
                    break
    # ── 分红 ──
    dividends: list[dict[str, Any]] = []
    for rep_year in ("2025", "2024", "2023"):
        rs = bs.query_dividend_data(symbol, year=rep_year, yearType="report")
        if rs is not None and rs.error_code == "0":
            while rs.next():
                rd = rs.get_row_data()
                if rd and rd[0]:
                    div = dict(zip(rs.fields, rd, strict=True))
                    pub = str(div.get("dividPlanAnnounceDate", ""))
                    if pub and _period_ok(pub):
                        dividends.append(div)

    def _f(val: Any) -> float | None:
        if val is None:
            return None
        if isinstance(val, float) and _math.isnan(val):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # baostock 返回的比率类字段已是分数（如 0.344620 = 34.46%），与 yfinance 对齐，
    # **不需要** /100 归一。绝对值字段原始单位保持（营收/净利 → 元对齐 yfinance）。
    indicators: dict[str, float | None] = {}
    if profit:
        _p = profit
        _pa = profit_annual
        indicators["roe"] = _f(_p.get("roeAvg"))
        indicators["gross_margin"] = _f(_p.get("gpMargin") or (_pa or {}).get("gpMargin"))
        indicators["net_margin"] = _f(_p.get("npMargin") or (_pa or {}).get("npMargin"))
        indicators["eps_ttm"] = _f(_p.get("epsTTM"))
        indicators["revenue"] = _f(_p.get("MBRevenue") or (_pa or {}).get("MBRevenue"))
        indicators["net_profit"] = _f(_p.get("netProfit") or (_pa or {}).get("netProfit"))
    if balance:
        indicators["current_ratio"] = _f(balance.get("currentRatio"))
        indicators["quick_ratio"] = _f(balance.get("quickRatio"))
        indicators["debt_to_equity"] = _f(balance.get("liabilityToAsset"))
        indicators["equity_multiplier"] = _f(balance.get("assetToEquity"))  # 权益乘数
    if growth:
        indicators["profit_yoy"] = _f(growth.get("YOYPNI"))
        indicators["revenue_yoy"] = _f(growth.get("YOYNI"))       # 归属净利润同比
        indicators["equity_yoy"] = _f(growth.get("YOYEquity"))    # 净资产同比
        indicators["asset_yoy"] = _f(growth.get("YOYAsset"))      # 总资产同比
        indicators["eps_yoy"] = _f(growth.get("YOYEPSBasic"))     # EPS 同比
    if cash_flow:
        indicators["ocf_to_revenue"] = _f(cash_flow.get("CFOToOR"))
        indicators["ocf_to_profit"] = _f(cash_flow.get("CFOToNP"))
    if operation:
        indicators["inventory_turnover"] = _f(operation.get("INVTurnRatio"))
        indicators["inventory_days"] = _f(operation.get("INVTurnDays"))
        indicators["receivables_turnover"] = _f(operation.get("NRTurnRatio"))
        indicators["receivables_days"] = _f(operation.get("NRTurnDays"))
        indicators["asset_turnover"] = _f(operation.get("AssetTurnRatio"))
    if dupont:
        indicators["dupont_roe"] = _f(dupont.get("dupontROE"))
        indicators["dupont_net_margin"] = _f(dupont.get("dupontNitogr"))
        indicators["dupont_asset_turnover"] = _f(dupont.get("dupontAssetTurn"))
        indicators["dupont_equity_multiplier"] = _f(dupont.get("dupontAssetStoEquity"))
        indicators["dupont_tax_burden"] = _f(dupont.get("dupontTaxBurden"))
        indicators["dupont_interest_burden"] = _f(dupont.get("dupontIntburden"))

    return {
        "_indicators": indicators,
        "_period": {"year": py, "quarter": pq} if py else None,
        "profit": profit,
        "balance": balance,
        "growth": growth,
        "cash_flow": cash_flow,
        "operation": operation,
        "dupont": dupont,
        "dividends": dividends,
    }


def _flatten_financial_abstract(
    raw: Any,
    *,
    as_of: datetime | None = None,
    publish_lag_days: int = FINANCIALS_PUBLISH_LAG_DAYS,
) -> dict[str, Any]:
    """转置财报表 → ``{指标名: 最新非空值}``；仅港股路径使用。"""
    import math

    if raw is None:
        return {}
    if not hasattr(raw, "columns"):
        return raw if isinstance(raw, dict) else {}

    cols = [str(c) for c in raw.columns]
    date_cols = sorted(
        (c for c in raw.columns if str(c).isdigit() and len(str(c)) == 8),
        key=lambda c: str(c),
        reverse=True,
    )
    if as_of is not None:
        date_cols = [c for c in date_cols if _period_publishable(str(c), as_of, publish_lag_days)]
        if not date_cols:
            return {}
    if "指标" not in cols or not date_cols:
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
            break
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


def _fetch_baostock_sync(
    *,
    symbol: str,
    frequency: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """同步调 baostock（证券宝）—— 免费零 key，A 股日/周/月 K 线。

    ``symbol`` 格式 ``"sh.600519"`` / ``"sz.000001"``（与 akshare 旧格式一致）。
    ``frequency`` 为 ``"d"/"w"/"m"``。
    ``start_date`` / ``end_date`` 为 ``YYYYMMDD``（akshare 上层格式），内部转
    ``YYYY-MM-DD``（baostock 要求）。
    返回 list[dict] 含 date/open/high/low/close/volume/amount 字段。
    """
    import baostock as bs

    # baostock 要求 YYYY-MM-DD 格式
    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

    _ensure_bs_login()

    rs = bs.query_history_k_data_plus(
        symbol,
        "date,open,high,low,close,volume,amount",
        start_date=start_fmt,
        end_date=end_fmt,
        frequency=frequency,
        adjustflag="3",
    )

    if rs is None or rs.error_code != "0":
        _logger.warning(
            "baostock_query_failed",
            symbol=symbol,
            frequency=frequency,
            start=start_fmt,
            end=end_fmt,
            error_code=getattr(rs, "error_code", None),
            error_msg=getattr(rs, "error_msg", None),
        )
        return []

    rows: list[dict[str, Any]] = []
    while rs.next():
        row_data = rs.get_row_data()
        if not row_data or row_data[0] == "":
            continue
        date_str, open_s, high_s, low_s, close_s, vol_s, amt_s = row_data[:7]
        rows.append({
            "date": date_str,
            "open": open_s,
            "high": high_s,
            "low": low_s,
            "close": close_s,
            "volume": vol_s,
            "amount": amt_s,
        })

    return rows


def _fetch_sync(
    *,
    prefix: str,
    code: str,
    period: str,
    start_str: str,
    end_str: str,
) -> list[dict[str, Any]]:
    """同步调数据源 —— 按市场前缀路由。

    单独抽函数让 ``asyncio.to_thread`` 序列化参数更明确，也方便测试 monkeypatch。

    路由：
    - A股（sh/sz）→ baostock（证券宝，免费零 key，日/周/月 + volume）
    - 港股（hk）  → akshare ``stock_hk_hist``（东财源，当前 push2his 失效）

    .. deprecated::
        ``stock_jp_hist`` / ``stock_uk_hist`` / ``stock_de_hist`` 在 akshare ≥1.18.63
        中已删除。日/英/德股由 orchestrator 路由到 yfinance。
    """
    import akshare as ak

    common = dict(
        symbol=code,
        period=period,
        start_date=start_str,
        end_date=end_str,
    )

    if prefix in ("sh", "sz"):
        # A股走 baostock（证券宝）。东财 push2his 2026-07 起失效，腾讯源仅日线且无 volume；
        # baostock 免费零 key、日/周/月全支持、有真实成交量。
        # symbol 格式 "sh.600519" / "sz.000001"（与 _parse_symbol 产物一致，零转换）。
        # period 已由上层 fetch_bars 经 _PERIOD_MAP 转为 "daily"/"weekly"/"monthly"
        _baostock_freq = {"daily": "d", "weekly": "w", "monthly": "m"}
        baostock_freq = _baostock_freq.get(period)
        if baostock_freq is None:
            raise NotImplementedError(
                f"baostock does not support period {period!r}; "
                f"supported: {sorted(_baostock_freq.keys())}"
            )
        return _fetch_baostock_sync(
            symbol=f"{prefix}.{code}",
            frequency=baostock_freq,
            start_date=start_str,
            end_date=end_str,
        )
    elif prefix == "hk":
        # 港股走东财 stock_hk_hist（push2his API，当前不可用；orchestrator 已将
        # hk 前缀默认路由到 yfinance，这里保留作 fallback——东财恢复后自动生效）
        df = ak.stock_hk_hist(adjust="", **common)
        if df is None or len(df) == 0:
            _logger.warning(
                "akshare_fetch_empty",
                prefix=prefix,
                code=code,
                period=period,
                start_str=start_str,
                end_str=end_str,
            )
            return []
        # df 非 None 且非空，安全调用 to_dict
        return df.to_dict(orient="records")  # type: ignore[no-any-return]
    elif prefix == "jp":
        # stock_jp_hist 在 akshare ≥1.18.63 已删除；orchestrator 将 jp 路由到 yfinance
        raise NotImplementedError(
            "akshare stock_jp_hist was removed in akshare >=1.18.63; "
            "use yfinance venue with symbol format 'CODE.T' (e.g. '6758.T')"
        )
    elif prefix == "uk":
        # stock_uk_hist 在 akshare ≥1.18.63 已删除
        raise NotImplementedError(
            "akshare stock_uk_hist was removed in akshare >=1.18.63; "
            "use yfinance venue with symbol format 'TICKER.L' (e.g. 'BARC.L')"
        )
    elif prefix == "de":
        # stock_de_hist 在 akshare ≥1.18.63 已删除
        raise NotImplementedError(
            "akshare stock_de_hist was removed in akshare >=1.18.63; "
            "use yfinance venue with symbol format 'TICKER.DE' (e.g. 'SAP.DE')"
        )
    else:
        raise ValueError(f"unreachable: prefix {prefix!r} should be filtered earlier")


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
    """同步取指数成分——A股三大指数走 baostock，其余走 akshare fallback。

    baostock 支持的指数代码：
    - ``"000300"`` → 沪深300（``bs.query_hs300_stocks()``）
    - ``"000016"`` → 上证50  （``bs.query_sz50_stocks()``）
    - ``"000905"`` → 中证500（``bs.query_zz500_stocks()``）

    baostock 无权重数据（weight 恒 None），其余指数退到原 akshare 东财源。
    """
    import akshare as ak
    import baostock as bs

    _BS_INDEX_MAP = {
        "000300": bs.query_hs300_stocks,
        "000016": bs.query_sz50_stocks,
        "000905": bs.query_zz500_stocks,
    }
    query_fn = _BS_INDEX_MAP.get(index_code)
    if query_fn is not None:
        _ensure_bs_login()
        try:
            rs = query_fn()
            if rs is not None and rs.error_code == "0":
                out: list[dict[str, Any]] = []
                while rs.next():
                    rd = rs.get_row_data()
                    if rd and rd[0]:
                        row = dict(zip(rs.fields, rd, strict=True))
                        raw_code = str(row.get("code", "")).strip()
                        out.append({
                            "code": _cn_symbol(raw_code),
                            "name": str(row.get("code_name", "")).strip() or None,
                            "weight": None,  # baostock 无权重
                        })
                if out:
                    return out
        except Exception as exc:
            _logger.warning("baostock_constituents_failed", index_code=index_code, error=str(exc))

    # akshare 东财 fallback（其他指数 / baostock 失败时）
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
        for k in keys:
            for c in cols:
                if k in c:
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
        code_str = str(raw).strip().zfill(6)
        if not code_str.isdigit() or len(code_str) != 6:
            continue
        out.append({
            "code": _cn_symbol(code_str),
            "name": str(row.get(name_col)).strip() if name_col else None,
            "weight": _to_float(row.get(weight_col)) if weight_col else None,
        })
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
    _bs_session_logout()


def get_connector() -> AkshareConnector:
    if _connector is None:
        raise RuntimeError("Akshare connector not initialized; call init_connector() first")
    return _connector

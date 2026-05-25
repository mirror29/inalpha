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
from datetime import UTC, datetime
from typing import Any

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "akshare"

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
        # akshare 没有 client 对象，import 即用；这里占位，将来加缓存 / cookie 时用
        pass

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

    async def close(self) -> None:
        # akshare 无连接对象需要关
        return None


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

"""FRED 宏观数据 connector —— 圣路易斯联储 80 万+ 经济序列。

为什么 FRED：

- **不可替代**：全球宏观数据权威源（Fed Funds / CPI / GDP / Unemployment / DXY / ...）
- **免费 + 稳定**：免费 key 注册即拿，120 req/min，几十年不挂
- **AI agent 友好**：series ID 是稳定字符串，prompt 里直接列出来调

**symbol 格式**（venue=``"fred"``）：直接用 FRED series ID。

| Series ID | 含义 | 频率 |
|---|---|---|
| ``DFF`` | Effective Federal Funds Rate（联邦基金利率） | daily |
| ``DGS10`` | 10-Year Treasury Yield | daily |
| ``DEXUSEU`` | USD/EUR 汇率 | daily |
| ``DEXJPUS`` | JPY/USD | daily |
| ``DEXCHUS`` | CNY/USD | daily |
| ``DTWEXBGS`` | 广义美元指数（替代 DXY） | daily |
| ``CPIAUCSL`` | US CPI（headline） | monthly |
| ``UNRATE`` | US 失业率 | monthly |
| ``GDP`` | US GDP | quarterly |
| ``M2SL`` | M2 货币供应 | monthly |

更多 series 见 https://fred.stlouisfed.org/

**数据形态适配**：FRED 是单值时间序列，没有 OHLCV。本 connector 把单值塞进 5 个
价格字段（``open == high == low == close == value``），``volume`` 填 0，让现有
``bars`` 表能直接吞下；下游消费 ``close`` 即可拿到原值。

**timeframe**：FRED series 的发布频率自身固定（daily / weekly / monthly / quarterly），
``timeframe`` 参数仅用于 backfill 估算 span；实际拉的是 series 原频率全量。

**key 缺失行为**：同 alpaca，``init_connector`` 返 None 不注册，services/data
正常启动。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from inalpha_shared import get_logger

from ._base import register_connector, unregister_connector

_logger = get_logger(__name__)

VENUE = "fred"

#: 给 backfill span 估算用的"名义周期"——FRED 实际频率以 series 本身为准。
TIMEFRAME_SECONDS: dict[str, int] = {
    "1d": 86400,
    "1wk": 604800,
    "1mo": 2_592_000,
    "1q": 7_776_000,
    "1y": 31_536_000,
}


class FredConnector:
    """fredapi.Fred 包装 —— 同步库走 ``asyncio.to_thread``。"""

    def __init__(self, api_key: str) -> None:
        from fredapi import Fred

        if not api_key:
            raise ValueError("FredConnector requires non-empty api_key")
        self._client = Fred(api_key=api_key)

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        """从 FRED 拉 series。

        Args:
            symbol: FRED series ID（如 ``"DFF"`` / ``"CPIAUCSL"``）
            timeframe: 仅用于 span 估算；FRED 返 series 自有频率
            since: UTC datetime；FRED 接 ``observation_start`` 字串
            limit: 截断尾部（FRED 不接 limit）

        Returns:
            list of ``(ts, value, value, value, value, 0.0)``——单值塞进 OHLC 4 字段，
            ``volume`` 强制 0；下游消费 ``close`` 拿到原值。
        """
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"fred: unsupported timeframe {timeframe!r}")

        _logger.debug(
            "fred_fetch_series",
            symbol=symbol,
            since=since.isoformat(),
            limit=limit,
        )

        rows = await asyncio.to_thread(
            _fetch_sync,
            client=self._client,
            symbol=symbol,
            since=since,
        )

        out: list[tuple[datetime, float, float, float, float, float]] = []
        for ts_raw, value in rows:
            if value is None:
                continue  # FRED 序列可能含 NaN（节假日 / 未发布）
            ts = _normalize_ts(ts_raw)
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            # 单值塞进 OHLC 4 字段，volume=0
            out.append((ts, v, v, v, v, 0.0))

        if limit and len(out) > limit:
            out = out[-limit:]
        return out

    async def close(self) -> None:
        # fredapi 内部用 requests session，自管理
        return None


def _fetch_sync(
    *,
    client: Any,
    symbol: str,
    since: datetime,
) -> list[tuple[Any, Any]]:
    """同步调 ``Fred.get_series`` —— 抽函数让 ``to_thread`` 序列化更明确。"""
    start_str = since.strftime("%Y-%m-%d")
    series = client.get_series(symbol, observation_start=start_str)
    if series is None or len(series) == 0:
        return []
    # series 是 pd.Series：index=date, value=float
    return list(series.items())


def _normalize_ts(ts_raw: Any) -> datetime:
    """FRED 给的 ts 是 ``pd.Timestamp``（无 tz，date-only），补 UTC。"""
    if hasattr(ts_raw, "to_pydatetime"):
        dt = ts_raw.to_pydatetime()
    elif isinstance(ts_raw, datetime):
        dt = ts_raw
    else:
        dt = datetime.fromisoformat(str(ts_raw))

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ---------- module-level singleton ----------

_connector: FredConnector | None = None


def init_connector(api_key: str = "") -> FredConnector | None:
    """启动时调一次。

    **key 缺失时返 None 不注册**——同 alpaca 模式，让 services/data 在没配 FRED key
    的 dev 环境下也能起；fred venue 自然不可达，返清晰错误。
    """
    global _connector
    if _connector is not None:
        raise RuntimeError("FRED connector already initialized")
    if not api_key:
        _logger.info(
            "fred_connector_skipped",
            reason="FRED_API_KEY not set",
        )
        return None
    _connector = FredConnector(api_key=api_key)
    register_connector(VENUE, _connector)
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    unregister_connector(VENUE)
    _connector = None


def get_connector() -> FredConnector:
    if _connector is None:
        raise RuntimeError("FRED connector not initialized; call init_connector() first")
    return _connector

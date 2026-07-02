"""Technical analyst —— K 线 + 简单指标（SMA / RSI）支撑下的短期立场。

D-9 起：multi-market 感知——同一个 analyst 在 crypto / 美股 / A 股 / 港股 / 全球 5 类
资产上自动调整指标解读（market_type 由 ``researchers.base.infer_asset_type`` 推断后
塞进 user prompt）。计算指标的 Python 代码完全通用（OHLCV 都一样），只在 prompt 上
做差别提示。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..researchers.base import infer_asset_type
from .base import Analyst

_SYSTEM = """
You are a technical analyst covering any asset class.

You receive recent OHLCV bars + indicator snapshot, plus the ``market_type``
(crypto / us_stock / cn_stock / hk_stock / global_stock). Adjust your reading
to the market's micro-structure conventions:

| market_type    | Notes                                                                 |
|----------------|-----------------------------------------------------------------------|
| crypto         | 24/7 markets, RSI 70/30 hard; gap-less; vol regimes shift fast        |
| us_stock       | Cash hours; RSI 65/35 useful; pre/post-market gap risk; SPY/VIX peers |
| cn_stock       | T+1, 涨跌停 ±10% (科创板 ±20%), open-call auction; 成交量 quality      |
| hk_stock       | T+0 cash; ADR/H-share arbitrage; HK rate sensitivity                  |
| global_stock   | Local cash hours; FX-translation distort; thinner liquidity tail      |

Use **only price action and technicals** —— do not invoke fundamentals or news.

You may also receive an ``effective_factors`` block: factors from a factor library
(pandas-ta / Alpha101 / qlib) **ranked by their measured predictive power** on this exact
symbol/timeframe — each has a current ``value``, ``rank_ic`` (time-series Rank IC vs forward
return; sign = direction), ``direction`` (+1 long / -1 short / 0), and ``strength`` (0-1).
When this block is present, **prefer it over the raw indicator_snapshot** and ground your
factors/stance in the factors that actually have predictive power (high |rank_ic|). When it
is absent or empty, fall back to reading the indicator_snapshot yourself. Never invent
rank_ic numbers — only cite what is given.

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence core conclusion",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 2-4 structured factors
    {
      "name": "sma20_above_sma50",                // snake_case identifier
      "kind": "momentum" | "mean_reversion" | "volatility" | "macro" | "sentiment",
      "value": 1.02,                              // numeric, OR "high"/"medium"/"low"
      "strength": 0.7,                            // 0-1, how strong this factor is
      "horizon": "intraday" | "swing" | "position",
      "explanation": "20-bar SMA crossed above 50-bar by 2%"
    }
  ]
}

Rules for factors:
- Output 2-4 factors. Each must be derivable from the indicator snapshot or bars given.
- "kind" classifies the factor mechanism:
    momentum         = trend / breakout / cross  (e.g. SMA cross, MACD up)
    mean_reversion   = RSI extreme, distance from moving average, BB squeeze
    volatility       = ATR spike, range expansion / contraction
- Numeric "value" is preferred; only use the string buckets when no number applies.
- Be terse. Refuse to fabricate indicators that weren't given.
""".strip()


class TechnicalAnalyst(Analyst):
    """技术分析 analyst（multi-market 感知）。"""

    type_id = "technical"

    def system_prompt(self) -> str:
        return _SYSTEM

    async def build_user_prompt(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        lookback_days: int,
    ) -> str:
        from_ts = as_of - timedelta(days=lookback_days)
        # D-13 · P0：优先从共享预取数据读 K 线（runner 前置拉取，避免重复往返）
        if self._shared is not None and self._shared.bars is not None:
            bars = self._shared.bars
        else:
            bars = await self._data.get_bars(
                venue=venue,
                symbol=symbol,
                timeframe=timeframe,
                from_ts=from_ts,
                to_ts=as_of,
                limit=2_000,
            )

        # 提炼最近 N 根 + 算几个粗指标喂给 LLM（factor 服务不可用时的兜底）
        recent = bars[-60:]
        closes = [float(b["close"]) for b in recent]
        snapshot = _build_indicator_snapshot(closes)

        market_type = infer_asset_type(venue=venue, symbol=symbol)

        # 接现成因子库（docs/miro/11）：取"经前瞻收益/IC 验证有效"的因子排序，优先喂这块
        effective_factors, factor_status = await self._fetch_effective_factors(
            venue=venue, symbol=symbol, timeframe=timeframe, as_of=as_of
        )

        return _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            num_bars=len(bars),
            recent=recent,
            snapshot=snapshot,
            market_type=market_type,
            effective_factors=effective_factors,
            factor_status=factor_status,
        )

    async def _fetch_effective_factors(
        self, *, venue: str, symbol: str, timeframe: str, as_of: datetime
    ) -> tuple[list[dict[str, Any]], str]:
        """返回 ``(top 有效因子, 状态)``。状态用于区分两种"空列表"，避免误导：

        - ``"unavailable"``：无 factor client 或 factor-service 不可达 → 真·降级
        - ``"insufficient"``：服务**正常**但样本不足 / 没有因子过有效性阈值
          （``available=True`` 但 ``top_factors=[]``，典型：新标的历史 < ~120 根）
        - ``"ok"``：有有效因子

        修复点：之前两种都返空 list → prompt 一律说"factor library unavailable"，
        让 agent/用户误以为服务挂了（实际只是数据不够）。
        """
        if self._factor is None:
            return [], "unavailable"
        # D-13 · P0：runner 预拉因子快照 → 跳过 DataClient 调 factor service
        if self._shared is not None and self._shared.factor_snapshot is not None:
            factors = self._shared.factor_snapshot
            return (factors, "ok") if factors else ([], "insufficient")
        snap = await self._factor.get_snapshot(
            venue=venue, symbol=symbol, timeframe=timeframe, as_of=as_of
        )
        if not snap.get("available"):
            return [], "unavailable"
        factors = snap.get("top_factors")
        factors = factors if isinstance(factors, list) else []
        return (factors, "ok") if factors else ([], "insufficient")


def _build_indicator_snapshot(closes: list[float]) -> dict[str, Any]:
    """计算简单指标快照（无 numpy 依赖）。"""
    n = len(closes)
    if n == 0:
        return {"available": False}

    last = closes[-1]
    # SMA：样本不够时返 None 而不是"半段平均"（D-8b' review B14）
    sma20 = sum(closes[-20:]) / 20 if n >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if n >= 50 else None

    # 简化 RSI(14)：n < 15 直接 None
    rsi14: float | None = None
    if n >= 15:
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, 15):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains.append(diff)
            else:
                losses.append(-diff)
        avg_gain = sum(gains) / 14 if gains else 0.0
        avg_loss = sum(losses) / 14 if losses else 0.0
        if avg_loss == 0:
            rsi14 = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi14 = 100.0 - 100.0 / (1.0 + rs)

    return {
        "available": True,
        "last_close": last,
        "sma20": sma20,
        "sma50": sma50,
        "rsi14": rsi14,
        "pct_change_5bar": _pct_change(closes, 5),
        "pct_change_20bar": _pct_change(closes, 20),
    }


def _pct_change(closes: list[float], lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    base = closes[-lookback - 1]
    if base <= 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    num_bars: int,
    recent: list[dict[str, Any]],
    snapshot: dict[str, Any],
    market_type: str,
    effective_factors: list[dict[str, Any]] | None = None,
    factor_status: str = "unavailable",
) -> str:
    """简洁、tokens 友好的格式。"""
    last_lines = "\n".join(
        f"  - {b.get('ts')} O={b.get('open')} H={b.get('high')} L={b.get('low')} "
        f"C={b.get('close')} V={b.get('volume')}"
        for b in recent[-10:]
    )
    return (
        f"asset: {symbol} @ {venue}\n"
        f"market_type: {market_type}\n"
        f"timeframe: {timeframe}\n"
        f"as_of: {as_of.isoformat()}\n"
        f"bars_total: {num_bars}\n\n"
        f"{_format_effective_factors(effective_factors, factor_status)}"
        f"indicator_snapshot:\n  {snapshot}\n\n"
        f"last_10_bars:\n{last_lines}\n\n"
        f"Output the required JSON only."
    )


def _as_float(v: Any) -> float:
    """容错转 float：None / 非数值 / NaN-ish 一律兜底 0.0，避免格式化时 TypeError。"""
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _format_effective_factors(
    factors: list[dict[str, Any]] | None, status: str = "unavailable"
) -> str:
    """渲染有效因子块（factor-service 给的真因子值 + 有效性）。

    空列表分两种、给**不同**提示，避免把"数据不足"误报成"服务挂了"（CR major fix）：
    - status == "insufficient"：服务正常但样本不够 / 无因子过有效性阈值
    - 其它（unavailable / 无 client / 不可达）：真·降级
    """
    if not factors:
        if status == "insufficient":
            return (
                "effective_factors: (factor service ran but no factor passed the effectiveness "
                "threshold — likely insufficient price history; read indicator_snapshot below)\n\n"
            )
        return (
            "effective_factors: (factor library unavailable — read indicator_snapshot below)\n\n"
        )
    lines = []
    for f in factors:
        # 防御：缺字段 / null（factor-service 跨版本灰度时 snapshot 可能缺 rank_ic 等）
        # 直接 `f.get('rank_ic'):.3f` 在 None 上会 TypeError 崩掉整条 deep_dive。
        rank_ic = _as_float(f.get("rank_ic"))
        strength = _as_float(f.get("strength"))
        lines.append(
            f"  - {f.get('name')} [{f.get('kind')}] "
            f"value={f.get('value')} rank_ic={rank_ic:.3f} "
            f"dir={f.get('direction')} strength={strength:.2f}"
        )
    body = "\n".join(lines)
    return (
        "effective_factors (ranked by measured predictive power on this symbol/timeframe; "
        "prefer these over raw indicators):\n"
        f"{body}\n\n"
    )

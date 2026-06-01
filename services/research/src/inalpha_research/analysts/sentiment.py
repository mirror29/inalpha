"""Sentiment analyst —— multi-market 感知。

D-9 起：

- **crypto**：保留原 Fear & Greed Index（alternative.me）反向逻辑
- **非 crypto**（us / cn / hk / global stock）：**LLM-only**——不抓外部数据，让 LLM
  用训练知识 + market_type 推断情绪极端（VIX 历史、SPY 卖单流、A股两融余额、港股
  Southbound 净流入等可以由 LLM 概念性引用，不强求具体数值）

为什么非 crypto 不接 VIX/恐慌指数：

- alternative.me 只覆盖 crypto；非 crypto 的等价（CNN FNG / VIX 反向）需要 FRED key
  或网页抓取，复杂度跳级；D-10 真要接的话挂在这层升级
- LLM-only sentiment 比"硬抛错回 neutral"叙事强得多——dev/demo 足够
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ..researchers.base import infer_asset_type
from .base import Analyst

_FNG_URL = "https://api.alternative.me/fng/"
_FETCH_TIMEOUT_S = 10.0
_DEFAULT_LIMIT = 30

_SYSTEM = """
You are a sentiment analyst covering any asset class.

You receive the asset + market_type (crypto / us_stock / cn_stock / hk_stock /
global_stock). Adjust your sentiment framework per market:

| market_type    | Sentiment anchors (contrarian where applicable)                            |
|----------------|----------------------------------------------------------------------------|
| crypto         | Fear & Greed Index (provided); funding rates; ETF inflow narrative         |
| us_stock       | VIX regime; AAII bull-bear; put/call ratio; flow-of-funds                  |
| cn_stock       | 两融余额 / 北向资金 流入流出; 涨停板情绪; 创业板 vs 主板 强弱             |
| hk_stock       | Southbound 净买入; HKD-rates 紧张度; AH 价差宽窄                            |
| global_stock   | Local index VIX / volatility regime; sector rotation extremes              |

Contrarian heuristic (universal):
- Extreme fear   → moderate bullish bias  (capitulation often near bottoms)
- Extreme greed  → moderate bearish bias  (euphoria often near tops)
- Sustained extreme reading raises strength

When ``crypto_fng`` data is provided in the user prompt, **anchor on it**.
When absent (non-crypto), use your training-time knowledge of the asset's
sentiment regime — be honest about uncertainty (lower confidence).

Return ONLY a JSON object with this exact shape:

{
  "stance": "bullish" | "bearish" | "neutral",
  "confidence": float in [0, 1],
  "summary": "1-2 sentence reading",
  "key_points": ["bullet 1", "bullet 2", ...],   // up to 5 items
  "factors": [                                    // 1-2 sentiment factors
    {
      "name": "fng_extreme_fear",                 // snake_case identifier
      "kind": "sentiment",
      "value": 18,                                // the FNG value or trend score
      "strength": 0.7,                            // 0-1 ; higher when extreme + sustained
      "horizon": "swing",
      "explanation": "FNG at 18 (Extreme Fear), 30d avg 35 — contrarian bullish"
    }
  ]
}

Never claim numeric values you weren't given. Confidence and factor.strength
should reflect data freshness and how extreme + sustained the reading is.
""".strip()


class SentimentAnalyst(Analyst):
    """多市场 sentiment analyst。"""

    type_id = "sentiment"

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
        market_type = infer_asset_type(venue=venue, symbol=symbol)

        # crypto → 拉 FNG；非 crypto → 拉 yfinance news 喂 LLM
        if market_type == "crypto":
            try:
                entries = await _fetch_fng(limit=_DEFAULT_LIMIT)
            except Exception:
                entries = []

            if not entries:
                # Fallback: web search for crypto sentiment when FNG is unavailable
                web_results = await self._data.get_web_search(
                    f"{symbol} crypto market sentiment fear greed 2026", max_results=5
                )
                return _format_user_prompt_llm_only(
                    symbol=symbol,
                    as_of=as_of,
                    market_type=market_type,
                    fng_note="(Fear & Greed API unavailable — using web search)",
                    news=[],
                    web_results=web_results,
                )
            latest = entries[0]
            recent_values = [int(e["value"]) for e in entries]
            trend = _summarize_trend(recent_values)
            return _format_user_prompt_with_fng(
                symbol=symbol,
                as_of=as_of,
                market_type=market_type,
                latest=latest,
                recent_values=recent_values,
                trend=trend,
            )

        # 非 crypto：拉 yfinance ticker news + web search，真新闻锚定 sentiment
        # symbol 不一定能直接给 yfinance（akshare 的 sh.600519 等格式不通）；
        # 这里直接用原 symbol 试一次；data-service 拉不到会返空 list，自然降级 LLM-only。
        news = await self._data.get_news(symbol=symbol, limit=8)
        web_news = await self._data.get_web_search(
            f"{symbol} stock news sentiment analysis", max_results=5
        )
        return _format_user_prompt_llm_only(
            symbol=symbol,
            as_of=as_of,
            market_type=market_type,
            fng_note=(
                f"(non-crypto market — no Fear & Greed; {len(news)} news headlines, {len(web_news)} web results)"
                if news or web_news
                else "(non-crypto market — no Fear & Greed; all sources returned empty)"
            ),
            news=news,
            web_results=web_news,
        )


async def _fetch_fng(*, limit: int) -> list[dict[str, Any]]:
    """拉 Fear & Greed 序列，最新在 index 0。"""
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, trust_env=False) as client:
        r = await client.get(_FNG_URL, params={"limit": limit})
        r.raise_for_status()
        payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected Fear & Greed payload shape: {type(payload).__name__}")
    return data


def _summarize_trend(values: list[int]) -> dict[str, Any]:
    """给 LLM 看的小指标：当前值 / 30 日均值 / 最大最小 / 7 日变化。"""
    if not values:
        return {"available": False}
    last = values[0]
    window = values[: min(len(values), 30)]
    avg30 = sum(window) / len(window)
    delta7 = (last - values[7]) if len(values) > 7 else None
    return {
        "available": True,
        "last": last,
        "avg_30d": round(avg30, 1),
        "min_30d": min(window),
        "max_30d": max(window),
        "delta_7d": delta7,
    }


def _format_user_prompt_with_fng(
    *,
    symbol: str,
    as_of: datetime,
    market_type: str,
    latest: dict[str, Any],
    recent_values: list[int],
    trend: dict[str, Any],
) -> str:
    return (
        f"asset: {symbol}\n"
        f"market_type: {market_type}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"crypto_fng:\n"
        f"  latest_value: {latest.get('value')}\n"
        f"  classification: {latest.get('value_classification')}\n"
        f"  timestamp: {latest.get('timestamp')}\n"
        f"  trend_snapshot: {trend}\n"
        f"  recent_30d_values (newest first): {recent_values}\n\n"
        f"Output the required JSON only."
    )


def _format_user_prompt_llm_only(
    *,
    symbol: str,
    as_of: datetime,
    market_type: str,
    fng_note: str,
    news: list[dict[str, Any]],
    web_results: list[dict[str, Any]] | None = None,
) -> str:
    news_block = _render_news_block(news)
    web_block = _render_web_results(web_results or [])
    return (
        f"asset: {symbol}\n"
        f"market_type: {market_type}\n"
        f"as_of: {as_of.isoformat()}\n\n"
        f"crypto_fng: {fng_note}\n\n"
        f"{news_block}\n"
        f"{web_block}\n"
        f"**Anchor sentiment on the news and web results above when present** "
        f"(recent / repeated negative-tone → bearish sentiment; positive flow → bullish).\n"
        f"When all data blocks are empty, fall back to training knowledge with **lower confidence**.\n\n"
        f"Output the required JSON only."
    )


def _render_news_block(news: list[dict[str, Any]]) -> str:
    """把 news items 渲染成 LLM 可读 block；空时返清晰占位。"""
    if not news:
        return "live_news: (none available — sentiment must come from training knowledge)\n"
    lines = ["live_news (newest first):"]
    for n in news:
        ts = n.get("published_at") or "?"
        title = (n.get("title") or "").strip()
        publisher = n.get("publisher") or ""
        if not title:
            continue
        lines.append(f"  - [{ts}] {publisher}: {title}")
    return "\n".join(lines) + "\n"


def _render_web_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    lines = ["web_search_results (latest):"]
    for r in results[:3]:
        title = r.get("title", "")[:100]
        snippet = r.get("snippet", "")[:200]
        lines.append(f"  - {title}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines) + "\n"

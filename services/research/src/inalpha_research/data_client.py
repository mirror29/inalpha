"""data-service HTTP client（research 专用）。

设计与 ``services/paper/src/inalpha_paper/data_client.py`` 同构 —— 直接拷贝并改名，
避免 services 之间互相 import（[docs/miro/03 §模块依赖图](../../../docs/miro/03-kernel-design.md)
强约束：service 间只能通过 HTTP，不互相 import）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from inalpha_shared.errors import InalphaError


class DataServiceError(InalphaError):
    code = "DATA_SERVICE_ERROR"
    status_code = 502


class DataClient:
    """data-service 薄包装。"""

    def __init__(
        self,
        base_url: str,
        jwt_token: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {jwt_token}"},
            timeout=timeout,
            trust_env=False,
        )

    async def __aenter__(self) -> DataClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_bars(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 10_000,
        fresh: bool = False,
    ) -> list[dict[str, Any]]:
        """``GET /bars`` —— bar dicts (data-service ``BarResponse`` schema)。

        Args:
            fresh: 默认 False —— orchestrator 已在 deep_dive 前做完 backfill
                （Step 0: ``data.get_bars(fresh=true)`` + ``data.backfill_bars``），
                分析师应信任 DB 缓存，不应各自触发 backfill。
                需要确保最新数据时（如手动回测）显式传 ``fresh=True``。

        Backfill 失败时静默继续（不抛）—— Yahoo 反爬 / akshare 代理偶发不应让整条
        deep_dive 链路 500，让 caller 在 DB 缓存基础上工作。
        """
        if fresh:
            await self._best_effort_backfill(
                venue=venue,
                symbol=symbol,
                timeframe=timeframe,
                from_ts=from_ts,
                to_ts=to_ts,
            )

        try:
            r = await self._client.get(
                "/bars",
                params={
                    "venue": venue,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "from_ts": from_ts.isoformat(),
                    "to_ts": to_ts.isoformat(),
                    "limit": limit,
                },
            )
        except httpx.RequestError as e:
            raise DataServiceError(
                f"failed to reach data-service: {e}",
                code="DATA_SERVICE_UNREACHABLE",
            ) from e

        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = {"message": r.text}
            raise DataServiceError(
                f"data-service {r.status_code}: {detail.get('message', 'unknown')}",
                code=detail.get("code", "DATA_SERVICE_ERROR"),
                details={"upstream_status": r.status_code, "upstream_body": detail},
            )

        result = r.json()
        if not isinstance(result, list):
            raise DataServiceError(
                f"unexpected response shape from data-service: {type(result).__name__}"
            )
        return result

    async def _best_effort_backfill(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> None:
        """先 POST /backfill/bars 补到 ``to_ts``——失败静默不抛。

        理由：backfill 是 "best effort 让数据更新"，不是分析的硬依赖。yfinance/
        akshare 偶发反爬 / 代理问题不应阻断整个 deep_dive。
        """
        try:
            await self._client.post(
                "/backfill/bars",
                json={
                    "venue": venue,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "from_ts": from_ts.isoformat(),
                    "to_ts": to_ts.isoformat(),
                },
                # backfill 跨度大时 30s 可能不够（yfinance 1d/180d ~5s，akshare 较慢）
                timeout=60.0,
            )
        except Exception:
            pass

    async def get_news(
        self,
        *,
        venue: str = "yfinance",
        symbol: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """``GET /news`` —— ticker-specific news 头条（按时间倒序）。

        失败（venue 不支持 / 网络 / 测试 mock 未注册）时返**空 list**，不抛——
        让 analyst 兜底走 LLM-only 而不是整条链路 500。
        """
        try:
            r = await self._client.get(
                "/news",
                params={"venue": venue, "symbol": symbol, "limit": limit},
            )
        except Exception:
            return []
        if r.status_code >= 400:
            return []
        try:
            payload = r.json()
        except Exception:
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else []

    async def get_fundamentals(self, venue: str, symbol: str) -> dict[str, Any]:
        """``GET /fundamentals`` —— 拉财报基本面数据。

        失败时返 ``{"available": False, ...}``（不阻断整条链路）。
        """
        try:
            r = await self._client.get(
                "/fundamentals", params={"venue": venue, "symbol": symbol}
            )
        except Exception:
            return {"available": False, "reason": "request failed"}
        if r.status_code >= 400:
            return {"available": False, "reason": f"upstream {r.status_code}"}
        try:
            return r.json()
        except Exception:
            return {"available": False, "reason": "invalid json"}

    async def get_web_search(
        self, query: str, max_results: int = 5
    ) -> list[dict[str, Any]]:
        """``GET /web/search`` —— web 搜索。

        失败时返空 list（不阻断整条链路）。
        """
        try:
            r = await self._client.get(
                "/web/search",
                params={"query": query, "max_results": max_results},
            )
        except Exception:
            return []
        if r.status_code >= 400:
            return []
        try:
            payload = r.json()
        except Exception:
            return []
        return payload.get("results", []) if isinstance(payload, dict) else []

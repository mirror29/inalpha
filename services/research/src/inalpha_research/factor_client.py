"""factor-service HTTP client（research 专用）。

与 ``data_client.py`` 同构 —— service 间只走 HTTP，不互相 import。analyst 用它拿
"当前对该标的有效的因子 + 前瞻收益/IC"，把研究结论从"对着 5 个写死指标编叙事"升级成
"有数据背书的有效因子"（见 docs/miro/11）。

容错优先：factor-service 不可用 / 报错时返回 ``{"available": False, ...}``，让 analyst
**降级回旧的指标快照**而不是让整条 deep_dive 链路 500。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class FactorClient:
    """factor-service 薄包装：只用到 ``POST /snapshot``。"""

    def __init__(self, base_url: str, jwt_token: str, *, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {jwt_token}"},
            timeout=timeout,
            trust_env=False,
        )

    async def __aenter__(self) -> FactorClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_snapshot(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        lookback_bars: int = 720,
        horizon_bars: int = 5,
        top_n: int | None = None,
    ) -> dict[str, Any]:
        """``POST /snapshot`` —— top-N 有效因子。失败返 ``{"available": False}`` 不抛。"""
        try:
            r = await self._client.post(
                "/snapshot",
                json={
                    "venue": venue,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "as_of": as_of.isoformat(),
                    "lookback_bars": lookback_bars,
                    "horizon_bars": horizon_bars,
                    "top_n": top_n,
                },
            )
        except Exception:
            return {"available": False, "reason": "request failed", "top_factors": []}
        if r.status_code >= 400:
            return {
                "available": False,
                "reason": f"upstream {r.status_code}",
                "top_factors": [],
            }
        try:
            payload = r.json()
        except Exception:
            return {"available": False, "reason": "invalid json", "top_factors": []}
        if not isinstance(payload, dict):
            return {"available": False, "reason": "unexpected shape", "top_factors": []}
        return payload

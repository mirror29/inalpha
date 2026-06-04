"""data-service HTTP client（factor 专用）。

与 ``services/research/.../data_client.py`` 同构 —— service 间只走 HTTP，不互相 import
（[docs/miro/03 §模块依赖图](../../../docs/miro/03-kernel-design.md)）。

因子计算读历史 K 线属**回测语义**：默认 ``fresh=False``（信任 DB 缓存，不各自触发
backfill），符合 CLAUDE.md §3.1 —— 历史回放显式 fresh=False。
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
    """data-service 薄包装：只用到 ``GET /bars``。"""

    def __init__(self, base_url: str, jwt_token: str = "", *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {jwt_token}"} if jwt_token else {},
            timeout=timeout,
            trust_env=False,  # 防本地代理把 localhost 劫持到 198.18.x
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
    ) -> list[dict[str, Any]]:
        """``GET /bars`` —— 返回 data-service ``BarResponse`` dict 列表（按 ts 升序）。"""
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

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
        fresh: bool = False,
    ) -> list[dict[str, Any]]:
        """``GET /bars`` —— 返回 data-service ``BarResponse`` dict 列表（按 ts 升序）。

        Args:
            fresh: 默认 False（历史回放语义，见 /compute 与显式 as_of 的 /score）。
                **factor.timing / factor.score 在"现在"做择时时必须 fresh=True**——否则
                用上一次 backfill 截止的 stale 尾巴算 Rank IC，返回的"当前因子方向"可能是
                几小时前的状态，违反 CLAUDE.md §3.1 金融时效性（无 stale 标记的 stale 输出 = bug）。
                fresh=True 先 best-effort ``POST /backfill/bars`` 补到 to_ts 再读。
        """
        if fresh:
            await self._best_effort_backfill(
                venue=venue, symbol=symbol, timeframe=timeframe, from_ts=from_ts, to_ts=to_ts
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
        """先 ``POST /backfill/bars`` 补到 to_ts —— 失败静默不抛（对齐 research/paper 模式）。

        backfill 是"best effort 让数据更新"而非硬依赖：交易所反爬 / 网络偶发不应让一次
        择时查询 500。补不上时退化到 DB 已有缓存（仍受样本不足保护）。
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
                timeout=60.0,
            )
        except Exception:
            pass

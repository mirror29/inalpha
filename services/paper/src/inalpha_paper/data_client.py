"""data-service 的 HTTP 客户端 wrapper。

简单 httpx 异步客户端，附带：

- ``Authorization: Bearer <jwt>`` 自动注入（forward 用户 token）
- error 时把 data-service 的 ``{code, message}`` 翻译成 ``InalphaError`` 子类
- 30s 默认超时

后续 D-7+ 长任务 / WS 订阅另外加 client。
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
    """data-service 的薄包装。"""

    def __init__(
        self,
        base_url: str,
        jwt_token: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        # trust_env=False：服务内部互调不走系统代理（避免 ClashX / corp proxy 把
        # localhost 也代理走）。外部 API（如 CCXT 到 Binance）由各自连接器自己管。
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

    async def get_ticker(
        self,
        *,
        venue: str,
        symbol: str,
    ) -> dict[str, Any]:
        """``GET /ticker`` —— 服务端取最新价（D-8a' 加，给 /orders/submit 自取 refPrice）。

        Returns dict with: ``venue, symbol, price, ts, source, is_stale, stale_seconds``。
        """
        try:
            r = await self._client.get(
                "/ticker",
                params={"venue": venue, "symbol": symbol},
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
        if not isinstance(result, dict):
            raise DataServiceError(
                f"unexpected ticker response shape: {type(result).__name__}"
            )
        return result

    async def get_fx(
        self,
        *,
        base: str,
        quote: str,
    ) -> dict[str, Any]:
        """``GET /fx`` —— 汇率查询（D-11，给跨币种 equity 折算用）。

        ``rate`` = 1 单位 ``base`` 折算成多少 ``quote``。

        Returns dict with: ``base, quote, rate, ts, source, is_stale, stale_seconds``。
        拿不到时 data 返 502 FX_UNAVAILABLE → 这里抛 ``DataServiceError``（caller 决定
        是否把该币种排除出 equity + warning，不静默用旧值）。
        """
        try:
            r = await self._client.get(
                "/fx",
                params={"base": base, "quote": quote},
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
        if not isinstance(result, dict):
            raise DataServiceError(
                f"unexpected fx response shape: {type(result).__name__}"
            )
        return result

    async def get_bars(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 10_000,
        fresh: bool = True,
    ) -> list[dict[str, Any]]:
        """``GET /bars`` —— 返回 list of bar dicts（schema 见 data-service.BarResponse）。

        Args:
            fresh: **金融时效性默认 True (D-9)**——先 POST /backfill/bars 把 ``to_ts``
                之前的最新 K 线补上，再读。避免回测 / 实时分析拿到 stale 数据。
                历史回测明确不需要最新数据时传 ``fresh=False``。

        Backfill 失败时静默继续（不抛）。
        """
        if fresh:
            try:
                await self.backfill_bars(
                    venue=venue,
                    symbol=symbol,
                    timeframe=timeframe,
                    from_ts=from_ts,
                    to_ts=to_ts,
                )
            except Exception:
                pass

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

    async def backfill_bars(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> dict[str, Any]:
        """``POST /backfill/bars`` —— 让 data-service 主动从交易所拉 + 入库。

        用途：``run_backtest`` 检测到空 bars 时自愈用（D-9 fix）。data-service
        backfill 自带 dedupe + UPSERT，多个 paper 工作流同时触发同 symbol 不会出错。
        """
        try:
            r = await self._client.post(
                "/backfill/bars",
                json={
                    "venue": venue,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "from_ts": from_ts.isoformat(),
                    "to_ts": to_ts.isoformat(),
                },
            )
        except httpx.RequestError as e:
            raise DataServiceError(
                f"failed to reach data-service /backfill/bars: {e}",
                code="DATA_SERVICE_UNREACHABLE",
            ) from e

        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = {"message": r.text}
            raise DataServiceError(
                f"data-service backfill {r.status_code}: {detail.get('message', 'unknown')}",
                code=detail.get("code", "DATA_BACKFILL_FAILED"),
                details={"upstream_status": r.status_code, "upstream_body": detail},
            )

        result = r.json()
        if not isinstance(result, dict):
            raise DataServiceError(
                f"unexpected backfill response shape: {type(result).__name__}"
            )
        return result

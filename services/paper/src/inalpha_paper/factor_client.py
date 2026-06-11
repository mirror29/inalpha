"""factor-service 的 HTTP 客户端 wrapper（ADR-0047 衰减巡检用）。

与 :mod:`data_client` 同范式：httpx 异步 + Bearer JWT + ``trust_env=False``
（服务内部互调不走系统代理）。封装巡检需要的两个端点：

- ``POST /score`` —— 指定 factor_ids 的有效性（**巡检血缘因子用**：snapshot 的
  去相关会把同质因子剪出 top-N，血缘因子可能被剪掉看不到，必须走 score）
- ``POST /snapshot`` —— top-N 有效因子（无血缘声明时拍"标的因子环境"基准用）

调用方注意：本 client 的失败**永远不该影响交易链路**——起跑拍基准和巡检都是
best-effort，factor 服务不可用时调用方应捕获 :class:`FactorServiceError` 跳过。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from inalpha_shared.errors import InalphaError


class FactorServiceError(InalphaError):
    code = "FACTOR_SERVICE_ERROR"
    status_code = 502


class FactorClient:
    """factor-service 的薄包装。"""

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

    async def __aenter__(self) -> FactorClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def score(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        factor_ids: list[str],
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """``POST /score`` —— 指定因子集的完整有效性（含每因子 decay_state）。

        巡检血缘因子用：不过 snapshot 去相关，请求的每个因子都有结果行。
        返回 ScoreResponse dict（``factors`` / ``as_of`` 等）。
        失败抛 :class:`FactorServiceError`，调用方自行降级。
        """
        body: dict[str, Any] = {
            "venue": venue,
            "symbol": symbol,
            "timeframe": timeframe,
            "factor_ids": factor_ids,
        }
        if as_of is not None:
            body["as_of"] = as_of.isoformat()
        return await self._post("/score", body)

    async def snapshot(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime | None = None,
        top_n: int | None = None,
    ) -> dict[str, Any]:
        """``POST /snapshot`` —— 当前最有效因子快照（top-N，已去相关）。

        无血缘声明的 run 拍"标的因子环境"基准用。返回 SnapshotResponse dict
        （``top_factors`` / ``available`` / ``reason`` / ``as_of`` 等）。
        """
        body: dict[str, Any] = {
            "venue": venue,
            "symbol": symbol,
            "timeframe": timeframe,
        }
        if as_of is not None:
            body["as_of"] = as_of.isoformat()
        if top_n is not None:
            body["top_n"] = top_n
        return await self._post("/snapshot", body)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.post(path, json=body)
        except httpx.RequestError as e:
            raise FactorServiceError(
                f"failed to reach factor-service: {e}",
                code="FACTOR_SERVICE_UNREACHABLE",
            ) from e

        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = {"message": r.text}
            raise FactorServiceError(
                f"factor-service {path} {r.status_code}: {detail.get('message', 'unknown')}",
                code=detail.get("code", "FACTOR_SERVICE_ERROR"),
                details={"upstream_status": r.status_code, "upstream_body": detail},
            )

        result = r.json()
        if not isinstance(result, dict):
            raise FactorServiceError(
                f"unexpected {path} response shape: {type(result).__name__}"
            )
        return result

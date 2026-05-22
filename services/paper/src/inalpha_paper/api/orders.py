"""``POST /orders/submit`` —— 单笔下单（in-memory，同步撮合）。

D-8a': ``ref_price`` 改 optional；省略时服务端调 data-service /ticker 自取最新价。
这把"取价 + 撮合"职责回收到服务端，避免 LLM 拿 stale / hallucinate refPrice。

设计动机见 [ADR-0012 plan-exec](../../../../docs/decisions/0012-plan-exec-separation.md)。

D-8a' 范围（变更）：

- ``ref_price`` optional：不传时调 data /ticker
- 透传 ticker.source / is_stale 到响应里，撮合可审计
- 显式传 ``ref_price`` 时短路 ticker 调用（测试 / 压测友好）

未来 D-8b 路径：

1. 加 Portfolio 单例累计持仓
2. 加 ``orders`` 表订单流水落盘
3. 把 stale 阈值变成 venue-level 配置
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import InalphaError, UnauthorizedError

from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..execution.order_executor import OrderExecutor
from ..schemas import SubmitOrderRequest, SubmitOrderResponse

router = APIRouter(tags=["orders"])


class RefPriceUnavailableError(InalphaError):
    code = "REF_PRICE_UNAVAILABLE"
    status_code = 400


@router.post("/orders/submit", response_model=SubmitOrderResponse)
async def post_submit_order(
    req: SubmitOrderRequest,
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    _user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> SubmitOrderResponse:
    """单笔下单：按 ``ref_price`` 立即撮合。

    ``ref_price`` 省略时服务端调 ``data /ticker`` 自取。data-service 返
    NO_PRICE_AVAILABLE 时本端点抛 REF_PRICE_UNAVAILABLE 让 caller 先 backfill。
    """
    ref_price = req.ref_price

    # 服务端兜底：caller 没传 ref_price 时调 data /ticker
    if ref_price is None:
        if not authorization or not authorization.startswith("Bearer "):
            raise UnauthorizedError("missing Authorization header")
        user_token = authorization.removeprefix("Bearer ").strip()

        async with DataClient(settings.data_service_url, user_token) as data_client:
            try:
                ticker = await data_client.get_ticker(venue=req.venue, symbol=req.symbol)
            except Exception as e:
                raise RefPriceUnavailableError(
                    f"failed to fetch ref_price for {req.symbol}@{req.venue}: {e}",
                    details={"venue": req.venue, "symbol": req.symbol},
                ) from e

        ref_price = float(ticker["price"])

    result = OrderExecutor.execute(
        venue=req.venue,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=req.quantity,
        price=req.price,
        ref_price=ref_price,
        fee_rate=req.fee_rate,
    )
    return SubmitOrderResponse(**result)  # type: ignore[arg-type]

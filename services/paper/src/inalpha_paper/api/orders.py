"""``POST /orders/submit`` —— D-8a 单笔下单（in-memory，同步撮合）。

设计动机见 [ADR-0012 plan-exec](../../../../docs/decisions/0012-plan-exec-separation.md)：
orchestration 层的 ``executeTradePlan`` tool 最终落到本端点，把"approved 的计划"翻成
"撮合 + 返回成交结果"。

D-8a 范围：

- **In-memory + stateless**：不写库、不维持持仓、不累计 PnL（D-8b 持久化时升级）
- **ref_price 显式传入**：撮合参考价由调用方提供（orchestration 层负责拉最新行情）
- **MARKET / LIMIT 两种**：复用 ``SimulatedExchange`` 的成交规则（[execution/order_executor.py](../execution/order_executor.py)）

后续 D-8b 升级路径：

1. 加 ``Portfolio`` 单例 → 累计持仓 / 现金
2. 加 ``orders`` 表 → 订单流水落盘
3. ``executeTradePlan`` 用 ``approval_token`` 鉴权（当前在 orchestration 层做了）
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user

from ..execution.order_executor import OrderExecutor
from ..schemas import SubmitOrderRequest, SubmitOrderResponse

router = APIRouter(tags=["orders"])


@router.post("/orders/submit", response_model=SubmitOrderResponse)
async def post_submit_order(
    req: SubmitOrderRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> SubmitOrderResponse:
    """单笔下单：按 ``ref_price`` 立即撮合（MARKET 必成、LIMIT 按价触发）。

    成交规则见 ``OrderExecutor`` docstring。本路由本身只做 JWT 校验 + schema
    路由，业务逻辑全在 ``OrderExecutor.execute()``。
    """
    result = OrderExecutor.execute(
        venue=req.venue,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=req.quantity,
        price=req.price,
        ref_price=req.ref_price,
        fee_rate=req.fee_rate,
    )
    return SubmitOrderResponse(**result)  # type: ignore[arg-type]

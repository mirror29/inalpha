"""``spot_guard`` —— 网关层现货 long-only 守门(禁裸空 / 禁超卖翻空)。

设计动机:

回测路径的撮合器 :class:`SimulatedExchange` 在 ``_try_fill`` 里会调
:meth:`Portfolio.can_afford_sell`(``portfolio.py``)——"spot 模式禁裸 SHORT":
SELL 量超过当前 LONG 持仓即拒。但 **live runner 与 ``POST /orders/submit``** 走的是
无状态纯函数 :class:`OrderExecutor`(``order_executor.py``),撮合前**不查持仓**,
成交后 ``apply_fill_to_positions_and_cash``(``fills.py``)又允许仓位变负 → 现货
long-only 策略的 SELL 在空仓时被照单成交成裸空、账户挂上策略平不掉的空头(漂移)。

本模块把"是否违反 spot long-only"提炼成一个纯函数,供 live / HTTP 两条写路径在撮合
**之前**调用,使它们与回测 ``can_afford_sell`` **完全同口径**——让 live==回测,堵住漂移。

边界约定:

- 只管 SELL(做空方向);BUY 恒放行(现金透支守门 ``can_afford_buy`` 是兄弟 gap,不在本模块范围)
- ``positions`` 表底层仍保留**负仓表示能力**(``apply_fill`` 不变),为未来 opt-in
  做空 / 保证金(后续单独设计)保留;本守门只在**网关层**拦截 spot long-only 违规,
  接合约 / margin 后可按 risk rule 配置放宽
"""
from __future__ import annotations

from decimal import Decimal

from inalpha_shared.errors import InalphaError


class InsufficientPositionError(InalphaError):
    """SELL 量超过当前 LONG 持仓(裸空 / 超卖翻空):现货 long-only 禁止。"""

    code = "INSUFFICIENT_POSITION"
    status_code = 409


def violates_spot_long_only(
    *,
    side: str,
    quantity: float | Decimal,
    current_qty: float | Decimal,
) -> bool:
    """判一笔订单是否违反现货 long-only(禁裸空 / 禁超卖翻空)。

    与 :meth:`Portfolio.can_afford_sell` 同口径:``side=="SELL"`` 且卖出量
    **严格大于**当前 LONG 持仓量时违规(等量平仓放行)。BUY 恒不违规。

    Parameters
    ----------
    side : str
        订单方向 ``"BUY"`` / ``"SELL"``。
    quantity : float | Decimal
        本笔订单数量(正数)。
    current_qty : float | Decimal
        当前持仓**带符号**数量(LONG 为正、SHORT 为负、flat 为 0)。

    Returns
    -------
    bool
        ``True`` = 违反 spot long-only,调用方应拒单。
    """
    if side != "SELL":
        return False
    return Decimal(str(quantity)) > Decimal(str(current_qty))

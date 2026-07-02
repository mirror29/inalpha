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

- SELL 侧:``violates_spot_long_only`` 禁裸空 / 禁超卖翻空
- BUY 侧:``violates_spot_buying_power`` 禁现金透支——账户各币种桶按 FX 折算成 base
  总可用现金后守门(桶允许为负 = 账户内隐式借计价货币,但**总折算现金不允许被买穿**;
  折算由调用方做,本模块只收折算结果保持纯函数)
- ``positions`` 表底层仍保留**负仓表示能力**(``apply_fill`` 不变),为未来 opt-in
  做空 / 保证金(后续单独设计)保留;本守门只在**网关层**拦截 spot long-only 违规,
  接合约 / margin 后可按 risk rule 配置放宽
"""
from __future__ import annotations

from decimal import Decimal

from inalpha_shared.errors import InalphaError

# BUY 守门保留 1% buffer(手续费 / 滑点 / 价格 jitter),与回测撮合层守门同精神。
SPOT_BUY_SAFETY_FACTOR = Decimal("0.99")


class InsufficientPositionError(InalphaError):
    """SELL 量超过当前 LONG 持仓(裸空 / 超卖翻空):现货 long-only 禁止。"""

    code = "INSUFFICIENT_POSITION"
    status_code = 409


class InsufficientCashError(InalphaError):
    """spot BUY 所需资金超过账户折算后总可用现金:禁透支买穿现金池。"""

    code = "INSUFFICIENT_CASH"
    status_code = 409


def violates_spot_long_only(
    *,
    side: str,
    quantity: float | Decimal,
    current_qty: float | Decimal,
    trading_mode: str = "spot",
) -> bool:
    """判一笔订单是否违反现货 long-only(禁裸空 / 禁超卖翻空)。

    与 :meth:`Portfolio.can_afford_sell` 同口径:``side=="SELL"`` 且卖出量
    **严格大于**当前 LONG 持仓量时违规(等量平仓放行)。BUY 恒不违规。

    ``trading_mode != "spot"``(如 ``"perp"`` 永续)→ **恒不违规**:做空合法,是否放行
    改由保证金购买力校验(``Portfolio.can_afford_sell`` perp 分支 / 上层 margin 守门)决定。

    Parameters
    ----------
    side : str
        订单方向 ``"BUY"`` / ``"SELL"``。
    quantity : float | Decimal
        本笔订单数量(正数)。
    current_qty : float | Decimal
        当前持仓**带符号**数量(LONG 为正、SHORT 为负、flat 为 0)。
    trading_mode : str
        ``"spot"``(默认,强制 long-only)或 ``"perp"``(放开做空,本守门短路返 False)。

    Returns
    -------
    bool
        ``True`` = 违反 spot long-only,调用方应拒单。
    """
    if trading_mode != "spot":
        return False
    if side != "SELL":
        return False
    return Decimal(str(quantity)) > Decimal(str(current_qty))


def violates_spot_buying_power(
    *,
    side: str,
    quantity: float | Decimal,
    ref_price: float | Decimal,
    fee_rate: float | Decimal,
    order_ccy_rate: Decimal | None,
    available_cash_base: Decimal,
    trading_mode: str = "spot",
    safety_factor: Decimal = SPOT_BUY_SAFETY_FACTOR,
) -> bool:
    """判一笔订单是否违反 spot 购买力(禁透支买穿现金池)。

    与回测撮合层 ``Portfolio.can_afford_buy`` 同口径:``notional + fee``(折算到
    base_currency)超过 ``available_cash_base × safety_factor`` 即违规。SELL 恒不违规
    (由 :func:`violates_spot_long_only` 管);``trading_mode != "spot"`` 恒不违规
    (perp 走保证金购买力校验)。

    Parameters
    ----------
    side : str
        订单方向 ``"BUY"`` / ``"SELL"``。
    quantity : float | Decimal
        本笔订单数量(正数)。
    ref_price : float | Decimal
        撮合参考价(订单计价货币计)。
    fee_rate : float | Decimal
        手续费率。
    order_ccy_rate : Decimal | None
        1 单位订单计价货币折算成多少 base_currency;``None`` = 汇率不可用,
        **fail-closed 视为违规**(算不出这笔订单值多少 base,宁拒不猜)。
    available_cash_base : Decimal
        账户各币种现金桶折算到 base_currency 后的总可用现金(拿不到汇率的桶已由
        调用方排除;可能为负 = 已透支,任何 BUY 必拒)。
    trading_mode : str
        ``"spot"``(默认)或 ``"perp"``(短路返 False)。
    safety_factor : Decimal
        可用现金折扣(默认 0.99 留 1% buffer)。

    Returns
    -------
    bool
        ``True`` = 违反 spot 购买力,调用方应拒单。
    """
    if trading_mode != "spot":
        return False
    if side != "BUY":
        return False
    if order_ccy_rate is None:
        return True
    notional = Decimal(str(quantity)) * Decimal(str(ref_price))
    required = (notional + notional * Decimal(str(fee_rate))) * order_ccy_rate
    return required > available_cash_base * safety_factor

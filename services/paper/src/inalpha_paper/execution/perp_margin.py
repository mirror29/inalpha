"""``perp_margin`` —— USDT-M 永续合约保证金 / 强平 / 资金费纯数学(模拟盘内)。

范围与口径(对齐做空/合约杠杆设计稿,实现期再核交易所最新分档表):

- 只做 **USDT-M 永续(perpetual)+ 逐仓(isolated)+ 单向(one-way)** 的简化模拟。
- **mark price** 为唯一定价(强平 / UPNL / funding 全用 mark,防插针式过度强平);
  回测拿不到 mark 时由调用方用 bar close 近似并显式标注失真,本模块不关心来源。
- 本模块**纯函数、无状态、无 DB**:像 ``spot_guard`` 一样先把数学验对,再被
  ``Portfolio`` / ``PositionGuard`` / 回测引擎消费。

关键口径(三条最易错,均按调研校准):

1. **维持保证金分档且与杠杆解耦**:``MM = |notional|×MMR(档) − cum(档)``,杠杆只进 IM。
2. **强平价统一双分支公式**(``side=+1`` 多 / ``−1`` 空),逐仓 ``TMM=UPNL_other=0``。
3. **funding 进现金流、不并入 UPNL**;``funding = mark_notional×rate``,正费率多头付空头。
"""
from __future__ import annotations

import math

# ─── 默认参数(实现期可挪进 PaperSettings 覆盖) ───

#: 杠杆上限(per-run 配置,越界拒)
DEFAULT_MAX_LEVERAGE: int = 20

#: 强平惩罚费率(按被强平名义价值扣,叠加普通手续费;惩罚"靠强平兜底"的策略)
DEFAULT_LIQUIDATION_PENALTY_RATE: float = 0.01

#: 强平安全垫(把保护性止损抬到强平价上方的比例,留 buffer 避免贴着强平价才动)
DEFAULT_LIQUIDATION_BUFFER: float = 0.05

#: 简化维持保证金分档表 ``(下界, 上界, MMR, cum)``,名义价值单位 USDT。
#: 取自 Binance BTCUSDT 真实前 3 档,档边界连续(见模块测试的连续性断言)。
#: 作 crypto 默认,后续可 per-symbol 覆盖。
MM_BRACKETS: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 50_000.0, 0.004, 0.0),
    (50_000.0, 600_000.0, 0.005, 50.0),
    (600_000.0, math.inf, 0.010, 3_050.0),
)


def bracket_for(notional_abs: float) -> tuple[float, float]:
    """按名义价值(绝对值)落档,返回 ``(MMR, cum)``。

    边界约定:命中 ``下界 <= notional < 上界`` 的第一档;超出最高档上界用最高档兜底。
    """
    n = abs(notional_abs)
    for _lo, hi, mmr, cum in MM_BRACKETS:
        if n < hi:
            return mmr, cum
    last = MM_BRACKETS[-1]
    return last[2], last[3]


def initial_margin(notional_abs: float, leverage: float) -> float:
    """初始保证金 ``IM = |notional| / leverage``(开仓占用,reserve 进 locked)。"""
    if leverage <= 0:
        raise ValueError(f"leverage must be > 0, got {leverage}")
    return abs(notional_abs) / leverage


def maintenance_margin(notional_abs: float) -> float:
    """维持保证金 ``MM = |notional|×MMR(档) − cum(档)``,**与 leverage 无关**。"""
    n = abs(notional_abs)
    mmr, cum = bracket_for(n)
    return n * mmr - cum


def liquidation_price(
    *,
    side: int,
    qty_abs: float,
    entry_price: float,
    wallet_balance: float,
) -> float:
    """逐仓强平价(统一双分支公式,Binance/Freqtrade dry-run 同款)。

    ``liq = (WB + cum − side×|amt|×entry) / (|amt|×MMR − side×|amt|)``

    Parameters
    ----------
    side : int
        ``+1`` 多头 / ``−1`` 空头。
    qty_abs : float
        持仓数量绝对值(> 0)。
    entry_price : float
        开仓均价。
    wallet_balance : float
        逐仓分配给该仓的钱包余额(含已计提 funding;逐仓 ``TMM=UPNL_other=0``)。

    Returns
    -------
    float
        强平价;``mark`` 穿越它即触发强平。分母为 0(理论边界)时返 ``nan``。
    """
    if side not in (1, -1):
        raise ValueError(f"side must be +1 or -1, got {side}")
    if qty_abs <= 0:
        raise ValueError(f"qty_abs must be > 0, got {qty_abs}")
    notional = qty_abs * entry_price
    mmr, cum = bracket_for(notional)
    num = wallet_balance + cum - side * qty_abs * entry_price
    den = qty_abs * mmr - side * qty_abs
    if den == 0:
        return math.nan
    return num / den


def is_liquidated(*, side: int, mark_price: float, liq_price: float) -> bool:
    """mark 是否已穿越强平价:多头 ``mark <= liq``、空头 ``mark >= liq`` → 强平。"""
    if side == 1:
        return mark_price <= liq_price
    return mark_price >= liq_price


def funding_payment(*, qty_signed: float, mark_price: float, funding_rate: float) -> float:
    """资金费**支付额**(正 = 从钱包扣、负 = 入账)。

    ``payment = qty_signed × mark × rate``——正费率(rate>0)时多头(qty>0)付出(正)、
    空头(qty<0)收取(负)。**进 cash 已实现现金流,不并入 UPNL**;只在结算时点对当时持仓计提。
    """
    return qty_signed * mark_price * funding_rate


def unrealized_pnl(*, qty_signed: float, entry_price: float, mark_price: float) -> float:
    """未实现盈亏 ``= (mark − entry) × qty_signed``(qty 带符号,做空为负 → 价跌则盈)。"""
    return (mark_price - entry_price) * qty_signed


def is_perp_symbol(symbol: str) -> bool:
    """是否 USDT-M 永续标的:ccxt 记法含 ``:`` 结算币后缀(如 ``BTC/USDT:USDT``)。"""
    return ":" in symbol

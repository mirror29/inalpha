"""``PositionGuard`` —— 框架级持仓保护止损（ADR-0052）。

独立于策略 alpha 的**灾难性兜底**：每根 bar 在 ``update_mark`` 之后检查当前持仓的浮盈率，
穿越阈值即提交全仓保护性出场单。**回测引擎与 live session 共用同一组件、同一阈值、挂在
同一逻辑点**，保证"回测≠模拟盘"的行为一致（这正是它存在的理由）。

设计要点（详 ADR-0052 §D2）：

- **宽兜底,不是紧止损**：默认硬止损放宽（0.20），封尾部风险而非切正常波动；贴行情的
  紧止损是策略层 alpha 的事，框架层只做灾难兜底。
- **默认只做亏损侧**：硬止损默认开；移动止损 / 止盈默认关（封上行偏 alpha，会伤趋势）。
- **Chandelier（吊灯）ATR 移动止损（ADR-0052 增补 A）**：除固定百分比 trailing，另有一档
  基于 ATR 的吊灯移动止损（``mark ≤ 最高价 − atr_mult × ATR``），止损位随波动自适应；
  **复用 ``trailing_stop_loss`` tag**（语义同为移动止损，避免新增 exit_reason 迁移），默认关。
- **不偷未来**：在 bar close 的 mark 判定，出场单**下一根 bar 撮合**（与策略下单同语义），
  与 live 只能在收盘 bar 行动完全对齐。
- **绕过开仓闸**：保护性出场直接走 ``EXECUTION_ENGINE_ENDPOINT``，**不经 RiskEngine**——
  guard 本身就是风控，不该被"挡开仓"的锁拦住（回撤熔断中恰恰最需要它平仓）。live 路径
  里 runner 对带保护性 tag 的单跳过 ``risk_guard.enforce``（仍保留 notional 硬上限）。
- **与既有 RiskRule 组合**：出场打 ``tag``（stop_loss / take_profit / trailing_stop_loss）
  经 ``close_detector`` → ``exit_reason``，自动喂给 StoplossGuard / Cooldown，天然实现
  "止损后不立刻回场"，无需新建再入场抑制。

双向化（perp）:spot 仍只保护多头;perp 多 / 空对称——硬止损按**带方向浮盈率**判,平多发
SELL、平空发 BUY;并叠加**维持保证金强平**（mark 穿越含 buffer 的强平价 → tag=liquidation）。
v1 局限:空头的移动止损 / 吊灯 / 止盈,以及强平罚金 / 逐仓破产 clamp（现金侧）留 Phase 2。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from ..execution import perp_margin
from ..execution.exchange import EXECUTION_ENGINE_ENDPOINT
from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from ..kernel.msgbus import MessageBus
from ..model.commands import SubmitOrderCommand
from ..model.data import Bar
from ..model.orders import GUARD_ORDER_PREFIX, Order, OrderSide, OrderType

if TYPE_CHECKING:
    from ..kernel.clock import Clock
    from .portfolio import Portfolio


@dataclass
class _ChandelierState:
    """单 instrument 的 chandelier 状态：Wilder RMA ATR + 开仓以来最高价。

    ATR 递推与 ``strategies/atr_channel.py`` 同口径：前 ``period`` 根 TR 均值做种子，之后
    指数式平滑。``update`` 在 bar close 调用——已知当根完整 OHLC，无 lookahead（与 guard
    "收盘判定、下一根撮合"语义一致）。持仓转 flat 时整个 state 被丢弃，下次开仓重新累积。
    """

    period: int
    atr: float | None = None
    highest_high: float = 0.0  # 止损距离基准（chandelier 标准用最高价）
    highest_close: float = 0.0  # 激活门基准（close-based，与百分比 trailing 同口径）
    _prev_close: float | None = None
    _tr_count: int = 0
    _tr_seed_sum: float = 0.0

    def update(self, bar: Bar) -> None:
        """用当根 bar 推进最高价 / 最高收盘 / ATR（Wilder RMA）。"""
        self.highest_high = max(self.highest_high, bar.high)
        self.highest_close = max(self.highest_close, bar.close)
        if self._prev_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
            if self.atr is None:
                self._tr_count += 1
                self._tr_seed_sum += tr
                if self._tr_count >= self.period:
                    self.atr = self._tr_seed_sum / self.period
            else:
                self.atr = (self.atr * (self.period - 1) + tr) / self.period
        self._prev_close = bar.close


class PositionGuard:
    """框架级持仓保护止损。被回测引擎与 live session 实例化。

    Args:
        msgbus: 与所属引擎共享的 MessageBus
        clock: 引擎时钟（出场单 ts_init 用）
        portfolio: 引擎的 Portfolio（只读当前持仓 + avg_open_price）
        stop_loss_pct: 单仓浮亏穿 ``-stop_loss_pct`` → 全平（None = 关）
        take_profit_pct: 单仓浮盈穿 ``+take_profit_pct`` → 全平（None = 关）
        trailing_stop_pct: 自峰值浮盈回撤 ``trailing_stop_pct`` → 全平（None = 关）
        chandelier_atr_mult: 吊灯移动止损倍数，``mark ≤ 最高价 − mult×ATR`` → 全平（None = 关）
        chandelier_atr_period: 吊灯 ATR 周期（默认 22，经典 chandelier 取值）
    """

    def __init__(
        self,
        msgbus: MessageBus,
        clock: Clock,
        portfolio: Portfolio,
        *,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        chandelier_atr_mult: float | None = None,
        chandelier_atr_period: int = 22,
        liquidation_buffer: float = perp_margin.DEFAULT_LIQUIDATION_BUFFER,
    ) -> None:
        for name, val in (
            ("stop_loss_pct", stop_loss_pct),
            ("take_profit_pct", take_profit_pct),
            ("trailing_stop_pct", trailing_stop_pct),
            ("chandelier_atr_mult", chandelier_atr_mult),
        ):
            if val is not None and val <= 0:
                raise ValueError(f"{name} must be positive or None, got {val}")
        if chandelier_atr_mult is not None and chandelier_atr_period < 2:
            raise ValueError(
                f"chandelier_atr_period must be >= 2, got {chandelier_atr_period}"
            )

        self._msgbus = msgbus
        self._clock = clock
        self._portfolio = portfolio
        self._stop_loss_pct = stop_loss_pct
        self._take_profit_pct = take_profit_pct
        self._trailing_stop_pct = trailing_stop_pct
        self._chandelier_atr_mult = chandelier_atr_mult
        self._chandelier_atr_period = chandelier_atr_period
        self._liquidation_buffer = liquidation_buffer
        self._strategy_id: StrategyId | None = None
        # 每 instrument 的峰值 mark 价（trailing 用，自峰值价格回撤口径）；持仓转 flat 时清除
        self._peak_mark: dict[InstrumentId, float] = {}
        # 每 instrument 的 chandelier 状态（ATR + 最高价，增补 A）；持仓转 flat 时清除
        self._chandelier_state: dict[InstrumentId, _ChandelierState] = {}
        # 已提交保护性出场、但持仓尚未平掉的 instrument（出场单下一根才撮合）。
        # 防 live 撮合延迟 / batch 行情下 evaluate 在持仓仍在时重复提交出场单（#91）。
        # 持仓转 flat（出场成交）时清除。
        self._pending_exit_insts: set[InstrumentId] = set()

    @staticmethod
    def from_thresholds(
        msgbus: MessageBus,
        clock: Clock,
        portfolio: Portfolio,
        *,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        trailing_stop_pct: float | None,
        chandelier_atr_mult: float | None = None,
        chandelier_atr_period: int = 22,
    ) -> PositionGuard | None:
        """工厂：所有闸阈值全为 None → 返 None（引擎据此退化为无 guard，向后兼容）。"""
        if (
            stop_loss_pct is None
            and take_profit_pct is None
            and trailing_stop_pct is None
            and chandelier_atr_mult is None
        ):
            return None
        return PositionGuard(
            msgbus,
            clock,
            portfolio,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            chandelier_atr_mult=chandelier_atr_mult,
            chandelier_atr_period=chandelier_atr_period,
        )

    def bind_strategy(self, strategy_id: StrategyId) -> None:
        """绑定所属策略 id。出场单用它提交，确保 on_position_closed 回到策略让其状态归零。

        **单策略约束**：guard 只持一个 ``_strategy_id``。绑第二个不同策略会让保护性出场
        归属错误（前策略状态不归零），故直接断言拒绝——多策略支持是引擎层整体未做项
        （CR #88，需与引擎多策略化一并推进）。调用方 ``BacktestEngine.add_strategy`` 也已
        在挂第二个策略时抛 RuntimeError，双层防呆。
        """
        if self._strategy_id is not None and self._strategy_id != strategy_id:
            raise RuntimeError(
                "PositionGuard 只支持单策略：不能绑定第二个不同的 strategy_id"
                f"（已绑 {self._strategy_id}，又试图绑 {strategy_id}）"
            )
        self._strategy_id = strategy_id

    def evaluate(self, bar: Bar) -> list[Order]:
        """检查 ``bar`` 对应 instrument 的持仓；触发则提交保护性出场并返回该单（否则空 list）。

        在引擎主循环 ``update_mark`` 之后调用，用 ``bar.close`` 作 mark。出场单经
        ``EXECUTION_ENGINE_ENDPOINT`` 进入 pending，**下一根 bar 撮合**（不偷未来）。
        """
        if self._strategy_id is None:
            return []

        inst = bar.instrument_id
        pos = self._portfolio.position(inst)
        if pos is None or pos.is_flat:
            self._peak_mark.pop(inst, None)
            self._chandelier_state.pop(inst, None)
            self._pending_exit_insts.discard(inst)  # 出场已成交（持仓平），解除去重标记
            return []

        qty = pos.quantity
        is_perp = self._portfolio.trading_mode == "perp"
        # spot 只保护多头（spot 结构上无空头）；perp 双向（多 / 空都保护 + 强平）
        if qty < 0 and not is_perp:
            return []

        # 已下保护性出场但持仓未平（撮合延迟）→ 跳过，不重复提交第二笔出场单（#91）
        if inst in self._pending_exit_insts:
            return []

        avg = pos.avg_open_price
        if avg <= 0:
            return []

        mark = bar.close
        signed = 1.0 if qty > 0 else -1.0
        # 带方向的浮盈率（正 = 盈）：多头 (mark-avg)/avg；空头取反（价跌则盈）
        pct = (mark - avg) / avg * signed

        # 维持保证金强平（perp，最高优先）：mark 穿越含 buffer 的强平价 → 全平，tag=liquidation
        if is_perp and self._liquidation_triggered(qty, avg, mark):
            return self._fire_exit(inst, qty, "liquidation", bar.ts_event)

        # trailing / chandelier 的「峰值价」口径按多头（最高价）实现；v1 仅对多头算，空头的
        # 移动止损 / 吊灯留 Phase 2（空头硬止损 + 强平已覆盖灾难兜底）。
        peak_mark = max(self._peak_mark.get(inst, mark), mark)
        self._peak_mark[inst] = peak_mark
        chan: _ChandelierState | None = None
        if self._chandelier_atr_mult is not None and qty > 0:
            chan = self._chandelier_state.get(inst)
            if chan is None:
                chan = _ChandelierState(period=self._chandelier_atr_period)
                self._chandelier_state[inst] = chan
            chan.update(bar)

        # stop_loss / take_profit 用带方向 pct（多空通用）；trailing / chandelier 仅多头
        tag = self._triggered_tag(pct, mark, peak_mark, avg, chan, is_long=qty > 0)
        if tag is None:
            return []

        return self._fire_exit(inst, qty, tag, bar.ts_event)

    def _fire_exit(
        self, inst: InstrumentId, qty: float, tag: str, ts_event: int
    ) -> list[Order]:
        """提交全仓保护性出场（平多 SELL / 平空 BUY），标记 pending + 清状态，返回该单。"""
        side = OrderSide.SELL if qty > 0 else OrderSide.BUY
        order = self._build_exit(inst, abs(qty), tag, side)
        self._submit(order, ts_event)
        self._peak_mark.pop(inst, None)
        self._chandelier_state.pop(inst, None)
        self._pending_exit_insts.add(inst)
        return [order]

    def _liquidation_triggered(self, qty: float, avg: float, mark: float) -> bool:
        """perp 维持保证金强平判定:mark 穿越含 buffer 的强平价(用 mark,非成交价)。

        逐仓简化:用分配保证金 IM 作 isolated wallet 估算强平价(与 fills 落库口径一致)。
        buffer 让触发**提前于**真实强平价(更保守):多头 liq×(1+buffer) 上抬、空头 ×(1−buffer) 下压。

        **v1 已知局限**:``wallet_balance`` 恒用**开仓 IM**,强平价不随 funding 计提 / 钱包缩水
        更新——长持仓 + 高频 funding 下真实 isolated wallet 缩水会让真强平价向 entry 靠近,本估算
        偏乐观(强平比真实晚触发)。短期 / 低费率可忽略;接动态 wallet_balance 见 #115。
        """
        side_i = 1 if qty > 0 else -1
        lev = self._portfolio.leverage
        im = abs(qty) * avg / lev
        liq = perp_margin.liquidation_price(
            side=side_i, qty_abs=abs(qty), entry_price=avg, wallet_balance=im,
        )
        if not math.isfinite(liq):
            return False
        liq_buffered = liq * (1 + side_i * self._liquidation_buffer)
        return perp_margin.is_liquidated(side=side_i, mark_price=mark, liq_price=liq_buffered)

    def cancel_pending_exit(self, inst: InstrumentId) -> None:
        """保护性出场单被 reject / cancel（如 live 路由 DB 失败）时调用，解除去重标记。

        否则出场没成交 → 持仓不 flat → evaluate 永久命中 ``_pending_exit_insts`` 跳过该
        instrument → 灾难止损静默失效（#94 CR）。解除后下一根 bar 重新评估，仍穿阈则重发。
        """
        self._pending_exit_insts.discard(inst)

    # ─── 内部 ───

    def _triggered_tag(
        self,
        pct: float,
        mark: float,
        peak_mark: float,
        avg: float,
        chan: _ChandelierState | None,
        *,
        is_long: bool = True,
    ) -> str | None:
        """按优先级判定触发的保护性 tag：硬止损 > chandelier > 百分比移动止损 > 止盈
        （None = 不触发）。chandelier 与百分比 trailing 通常二选一配置，同配时按此序谁先穿阈谁先触发。

        ``pct`` 已是**带方向的浮盈率**（空头取过反）,故硬止损 / 止盈多空通用;trailing / chandelier
        的峰值口径目前按多头实现,``is_long=False``（空头）时跳过这两档（Phase 2）。
        """
        if self._stop_loss_pct is not None and pct <= -self._stop_loss_pct:
            return "stop_loss"
        if not is_long:
            # 空头 v1 只走硬止损（+ 上层强平）;移动止损 / 吊灯 / 止盈留 Phase 2
            if self._take_profit_pct is not None and pct >= self._take_profit_pct:
                return "take_profit"
            return None
        # chandelier（吊灯）ATR 移动止损（增补 A）：止损位 = 开仓以来最高价 − mult×ATR，随波动
        # 自适应。激活门用「曾收盘进盈利区」（highest_close > 成本）——与百分比 trailing 的
        # close-based peak_mark>avg 同口径，避免单根上影线（high>avg 但 close<avg）误激活
        # （#97 CR）；止损距离仍用 highest_high（chandelier 标准）。ATR 种子就绪后才判；复用
        # trailing_stop_loss tag（语义同为移动止损，避免新增 exit_reason 迁移）。
        if (
            self._chandelier_atr_mult is not None
            and chan is not None
            and chan.atr is not None
            and chan.highest_close > avg
            and mark <= chan.highest_high - self._chandelier_atr_mult * chan.atr
        ):
            return "trailing_stop_loss"
        # 百分比移动止损：仅在仓位「曾进入盈利区」（峰值价 > 成本）后才生效——锁的是已有利润；
        # 用自峰值价格的回撤幅度判定（peak_mark - mark) / peak_mark），不掺成本基准。
        if (
            self._trailing_stop_pct is not None
            and peak_mark > avg  # 曾进盈利区(avg>0 已在 evaluate 守门,故 peak_mark>0 恒真)
            and (peak_mark - mark) / peak_mark >= self._trailing_stop_pct
        ):
            return "trailing_stop_loss"
        if self._take_profit_pct is not None and pct >= self._take_profit_pct:
            return "take_profit"
        return None

    def _build_exit(
        self, inst: InstrumentId, quantity: float, tag: str, side: OrderSide,
    ) -> Order:
        return Order(
            # GUARD_ORDER_PREFIX 是风控豁免的「不可仿冒」第二因子（见 is_protective_order）
            client_order_id=ClientOrderId(f"{GUARD_ORDER_PREFIX}{inst.symbol}-{uuid4().hex[:8]}"),
            instrument_id=inst,
            side=side,  # 平多 = SELL / 平空 = BUY
            type=OrderType.MARKET,
            quantity=quantity,
            tag=tag,
        )

    def _submit(self, order: Order, ts_event: int) -> None:
        # 直发 ExecutionEngine：绕过 in-process RiskEngine 的"挡开仓"锁——保护性平仓
        # 必须能在回撤熔断锁期内执行（ADR-0052 §D4）。
        assert self._strategy_id is not None  # evaluate 已守门
        self._msgbus.send(
            EXECUTION_ENGINE_ENDPOINT,
            SubmitOrderCommand(
                order=order,
                strategy_id=self._strategy_id,
                ts_init=ts_event,
            ),
        )

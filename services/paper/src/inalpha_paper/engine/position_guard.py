"""``PositionGuard`` —— 框架级持仓保护止损（ADR-0052）。

独立于策略 alpha 的**灾难性兜底**：每根 bar 在 ``update_mark`` 之后检查当前持仓的浮盈率，
穿越阈值即提交全仓保护性出场单。**回测引擎与 live session 共用同一组件、同一阈值、挂在
同一逻辑点**，保证"回测≠模拟盘"的行为一致（这正是它存在的理由）。

设计要点（详 ADR-0052 §D2）：

- **宽兜底,不是紧止损**：默认硬止损放宽（0.20），封尾部风险而非切正常波动；贴行情的
  紧止损是策略层 alpha 的事，框架层只做灾难兜底。
- **默认只做亏损侧**：硬止损默认开；移动止损 / 止盈默认关（封上行偏 alpha，会伤趋势）。
- **不偷未来**：在 bar close 的 mark 判定，出场单**下一根 bar 撮合**（与策略下单同语义），
  与 live 只能在收盘 bar 行动完全对齐。
- **绕过开仓闸**：保护性出场直接走 ``EXECUTION_ENGINE_ENDPOINT``，**不经 RiskEngine**——
  guard 本身就是风控，不该被"挡开仓"的锁拦住（回撤熔断中恰恰最需要它平仓）。live 路径
  里 runner 对带保护性 tag 的单跳过 ``risk_guard.enforce``（仍保留 notional 硬上限）。
- **与既有 RiskRule 组合**：出场打 ``tag``（stop_loss / take_profit / trailing_stop_loss）
  经 ``close_detector`` → ``exit_reason``，自动喂给 StoplossGuard / Cooldown，天然实现
  "止损后不立刻回场"，无需新建再入场抑制。

局限：仅 spot long；short / 合约对称版留待后续（short 当前 no-op）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from ..execution.exchange import EXECUTION_ENGINE_ENDPOINT
from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from ..kernel.msgbus import MessageBus
from ..model.commands import SubmitOrderCommand
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType

if TYPE_CHECKING:
    from ..kernel.clock import Clock
    from .portfolio import Portfolio

#: 保护性出场 tag 集合（与 closed_trades.exit_reason CHECK 集合、StoplossGuardRule 对齐）。
#: live_runner 据此判定一笔单是否为框架兜底出场（跳过开仓闸 enforce）。
PROTECTIVE_EXIT_TAGS: frozenset[str] = frozenset(
    {"stop_loss", "take_profit", "trailing_stop_loss"}
)


class PositionGuard:
    """框架级持仓保护止损。被回测引擎与 live session 实例化。

    Args:
        msgbus: 与所属引擎共享的 MessageBus
        clock: 引擎时钟（出场单 ts_init 用）
        portfolio: 引擎的 Portfolio（只读当前持仓 + avg_open_price）
        stop_loss_pct: 单仓浮亏穿 ``-stop_loss_pct`` → 全平（None = 关）
        take_profit_pct: 单仓浮盈穿 ``+take_profit_pct`` → 全平（None = 关）
        trailing_stop_pct: 自峰值浮盈回撤 ``trailing_stop_pct`` → 全平（None = 关）
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
    ) -> None:
        for name, val in (
            ("stop_loss_pct", stop_loss_pct),
            ("take_profit_pct", take_profit_pct),
            ("trailing_stop_pct", trailing_stop_pct),
        ):
            if val is not None and val <= 0:
                raise ValueError(f"{name} must be positive or None, got {val}")

        self._msgbus = msgbus
        self._clock = clock
        self._portfolio = portfolio
        self._stop_loss_pct = stop_loss_pct
        self._take_profit_pct = take_profit_pct
        self._trailing_stop_pct = trailing_stop_pct
        self._strategy_id: StrategyId | None = None
        # 每 instrument 的峰值 mark 价（trailing 用，自峰值价格回撤口径）；持仓转 flat 时清除
        self._peak_mark: dict[InstrumentId, float] = {}

    @staticmethod
    def from_thresholds(
        msgbus: MessageBus,
        clock: Clock,
        portfolio: Portfolio,
        *,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        trailing_stop_pct: float | None,
    ) -> PositionGuard | None:
        """工厂：三个阈值全为 None → 返 None（引擎据此退化为无 guard，向后兼容）。"""
        if stop_loss_pct is None and take_profit_pct is None and trailing_stop_pct is None:
            return None
        return PositionGuard(
            msgbus,
            clock,
            portfolio,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
        )

    def bind_strategy(self, strategy_id: StrategyId) -> None:
        """绑定所属策略 id。出场单用它提交，确保 on_position_closed 回到策略让其状态归零。

        **单值（last-bind wins）**：沿用引擎契约「单 strategy 单 instrument per session」
        （见 ``BacktestEngine`` / ``Portfolio`` docstring）。多策略场景下 guard 出场单会
        全部归属最后 bind 的策略——多策略支持是引擎层整体未做项（CR #88 medium，非本闸单独
        修，需与引擎多策略化一并推进）。
        """
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
            return []

        # 仅 spot long；short 留待合约阶段（no-op，避免对 short 误平）
        if pos.quantity <= 0:
            return []

        avg = pos.avg_open_price
        if avg <= 0:
            return []

        mark = bar.close
        pct = (mark - avg) / avg
        # trailing 用「自峰值价格的回撤」口径（不是自成本基准的收益率降幅）：避免大盈利下
        # 触发远比 trailing_stop_pct 直觉更激进（CR #88 medium）。峰值价跟 mark 走。
        peak_mark = max(self._peak_mark.get(inst, mark), mark)
        self._peak_mark[inst] = peak_mark

        tag = self._triggered_tag(pct, mark, peak_mark, avg)
        if tag is None:
            return []

        order = self._build_exit(inst, pos.quantity, tag)
        self._submit(order, bar.ts_event)
        # 出场单已下，清峰值（持仓将于下一根平掉；防同 instrument 状态泄漏）
        self._peak_mark.pop(inst, None)
        return [order]

    # ─── 内部 ───

    def _triggered_tag(
        self, pct: float, mark: float, peak_mark: float, avg: float
    ) -> str | None:
        """按优先级判定触发的保护性 tag：硬止损 > 移动止损 > 止盈（None = 不触发）。"""
        if self._stop_loss_pct is not None and pct <= -self._stop_loss_pct:
            return "stop_loss"
        # 移动止损：仅在仓位「曾进入盈利区」（峰值价 > 成本）后才生效——锁的是已有利润；
        # 用自峰值价格的回撤幅度判定（peak_mark - mark) / peak_mark），不掺成本基准。
        if (
            self._trailing_stop_pct is not None
            and peak_mark > avg
            and peak_mark > 0
            and (peak_mark - mark) / peak_mark >= self._trailing_stop_pct
        ):
            return "trailing_stop_loss"
        if self._take_profit_pct is not None and pct >= self._take_profit_pct:
            return "take_profit"
        return None

    def _build_exit(self, inst: InstrumentId, quantity: float, tag: str) -> Order:
        return Order(
            client_order_id=ClientOrderId(f"guard-{inst.symbol}-{uuid4().hex[:8]}"),
            instrument_id=inst,
            side=OrderSide.SELL,  # spot long-only：平多 = 卖出
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

"""``RiskEngine`` —— 执行层风控编排器。

D-5 起为 pass-through 占位；[ADR-0006](../../../../docs/miro/decisions/0006-risk-rules.md)
立项后加入 `RiskRule` 列表，对 `SubmitOrderCommand` 做前置闸门。

行为：

- `rules=None` 或空列表 → 退化为 pass-through（向后兼容 D-5 ~ D-8 老调用方）
- 提供 `rules` + `clock` → 每条 `SubmitOrderCommand` 先过 3 层（global / market / symbol）
  规则；任意命中即 publish `OrderRejected` 到 `events.order.<strategy_id>` topic，
  **不转发**到 ExecutionEngine

[refs/freqtrade.md §6.2](../../../../docs/miro/refs/freqtrade.md) 是设计灵感源（GPL-3.0，
本文件只借鉴设计，不复制源码）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.msgbus import MessageBus
from ..model.commands import CancelOrderCommand, ModifyOrderCommand, SubmitOrderCommand
from ..model.events import OrderRejected
from ..model.orders import OrderSide
from ..strategy.base import RISK_ENGINE_ENDPOINT
from .exchange import EXECUTION_ENGINE_ENDPOINT
from .risk_rules import RiskRule, RiskVerdict
from .risk_rules.base import Side

if TYPE_CHECKING:
    from ..kernel.clock import Clock


class RiskEngine:
    """执行层风控编排器。"""

    def __init__(
        self,
        msgbus: MessageBus,
        *,
        rules: list[RiskRule] | None = None,
        clock: Clock | None = None,
        starting_balance: float = 10_000.0,
    ) -> None:
        self._msgbus = msgbus
        self._rules: list[RiskRule] = list(rules) if rules else []
        self._clock = clock
        self._starting_balance = starting_balance

        if self._rules and self._clock is None:
            raise ValueError(
                "RiskEngine: 提供 rules 时必须同时提供 clock（rule 的 check_* 需要 now）"
            )

        msgbus.register_endpoint(RISK_ENGINE_ENDPOINT, self._handle)

    # ─── 命令处理 ───

    def _handle(self, msg: object) -> None:
        if isinstance(msg, SubmitOrderCommand) and self._rules:
            if self._check_and_maybe_reject(msg):
                return
        if isinstance(msg, SubmitOrderCommand | CancelOrderCommand | ModifyOrderCommand):
            self._msgbus.send(EXECUTION_ENGINE_ENDPOINT, msg)

    # ─── 规则编排 ───

    def _check_and_maybe_reject(self, cmd: SubmitOrderCommand) -> bool:
        """3 层依次检查；任意命中即 publish OrderRejected 并返回 True。"""
        assert self._clock is not None  # __init__ 校验
        now = self._clock.now()
        order = cmd.order
        side: Side = "long" if order.side == OrderSide.BUY else "short"
        balance = self._starting_balance

        # global → market → symbol（freqtrade 同顺序：全局更优先）
        for rule in self._rules:
            if rule.has_global_check:
                verdict = rule.check_global(now, side, balance)
                if verdict is not None:
                    self._publish_rejection(cmd, verdict)
                    return True

        for rule in self._rules:
            if rule.has_market_check:
                verdict = rule.check_market(order.instrument_id.venue, now, side, balance)
                if verdict is not None:
                    self._publish_rejection(cmd, verdict)
                    return True

        for rule in self._rules:
            if rule.has_symbol_check:
                verdict = rule.check_symbol(order.instrument_id, now, side, balance)
                if verdict is not None:
                    self._publish_rejection(cmd, verdict)
                    return True

        return False

    def _publish_rejection(
        self, cmd: SubmitOrderCommand, verdict: RiskVerdict
    ) -> None:
        reason = f"[{verdict.rule_name}] {verdict.reason}"
        self._msgbus.publish(
            f"events.order.{cmd.strategy_id}",
            OrderRejected(
                client_order_id=cmd.order.client_order_id,
                strategy_id=cmd.strategy_id,
                ts_event=cmd.ts_init,
                ts_init=cmd.ts_init,
                reason=reason,
            ),
        )

    # ─── inspection（测试用） ───

    @property
    def rule_names(self) -> list[str]:
        return [r.name for r in self._rules]

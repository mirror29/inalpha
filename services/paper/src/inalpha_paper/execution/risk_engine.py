"""``RiskEngine`` —— D-5 简化版 pass-through。

设计依据 [refs/nautilus.md §6](../../../../docs/refs/nautilus.md)：所有 SubmitOrderCommand
**必经** RiskEngine endpoint。D-5 不带规则，只做转发。

真正的风控规则化校验在 [ADR-0011 permission](../../../../docs/decisions/0011-permission-rules.md) /
[ADR-0010 hooks](../../../../docs/decisions/0010-orchestration-hooks.md) 体系里，
落地在 ``packages/orchestration``（Mastra 层）。本服务的 RiskEngine 在 D-7+ 加业务级
快校验（max position / max notional / 速率限流）。
"""
from __future__ import annotations

from ..kernel.msgbus import MessageBus
from ..model.commands import CancelOrderCommand, ModifyOrderCommand, SubmitOrderCommand
from ..strategy.base import RISK_ENGINE_ENDPOINT
from .exchange import EXECUTION_ENGINE_ENDPOINT


class RiskEngine:
    """Pass-through 风控引擎（D-5 起步形态）。"""

    def __init__(self, msgbus: MessageBus) -> None:
        self._msgbus = msgbus
        msgbus.register_endpoint(RISK_ENGINE_ENDPOINT, self._handle)

    def _handle(self, msg: object) -> None:
        # D-5：直接转发到 ExecutionEngine。
        # D-7+：在这里加规则化校验（max position / max notional / 速率限流 / fail-safe）。
        if isinstance(msg, SubmitOrderCommand | CancelOrderCommand | ModifyOrderCommand):
            self._msgbus.send(EXECUTION_ENGINE_ENDPOINT, msg)

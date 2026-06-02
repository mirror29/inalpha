"""Bull/Bear researcher 与辩论协调器。

辩论是 6 个 analyst 出完 brief 之后的环节：让 Bull / Bear 两个 LLM 角色
**轮换发言**，每轮可以读到对方上一轮论点 + 全部 6 个 analyst brief。
最终把发言序列（``list[DebateTurn]``）传给 Manager 做综合 rating。

灵感：``TauricResearch/TradingAgents`` 的 ``InvestDebateState`` 双方对喷拓扑，
去掉 LangGraph 依赖、用纯 asyncio 协调（保持 Inalpha 的极简栈）。
"""
from .base import Researcher
from .bear import BearResearcher
from .bull import BullResearcher

__all__ = [
    "BearResearcher",
    "BullResearcher",
    "Researcher",
]

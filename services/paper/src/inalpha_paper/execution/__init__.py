"""执行链：Strategy → RiskEngine → ExecutionEngine → Gateway → Exchange / 真实经纪商。

当前内容：

- ``Gateway`` 抽象 + ``SimulatedExchange``（一并实现 venue 撮合）
- ``RiskEngine`` —— 内核内风控守门（Order 进 Execution 前必经）
- ``ExecutionEngine`` —— Order 状态机管理 + 路由
- 规则化风控已落地在 ``risk_rules/``：回测路径由 ``RiskEngine`` 执行、HTTP 路径由
  ``RiskGuard`` 执行并落 ``risk_locks``；运行状态见 ``docs/04-current-state.md``

没有 ``LiveGateway``：不接真实经纪商，模拟盘成交走 ``SimulatedExchange`` + DB 侧 executor。
"""
from .exchange import EXECUTION_ENGINE_ENDPOINT, SimulatedExchange
from .execution_engine import ExecutionEngine
from .gateway import Gateway
from .risk_engine import RiskEngine

__all__ = [
    "EXECUTION_ENGINE_ENDPOINT",
    "ExecutionEngine",
    "Gateway",
    "RiskEngine",
    "SimulatedExchange",
]

"""执行链：Strategy → RiskEngine → ExecutionEngine → Gateway → Exchange / 真实经纪商。

D-5 范围：

- ``Gateway`` 抽象 + ``SimulatedExchange``（一并实现 venue 撮合，回测专用）
- ``RiskEngine`` —— D-5 简化版 pass-through，规则化校验在 ADR-0011 落地（Mastra 编排层）
- ``ExecutionEngine`` —— Order 状态机管理 + 路由

D-6+ 范围：

- ``LiveGateway`` 接 Binance via CCXT
- Risk 规则化校验（hook + permission 体系）
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

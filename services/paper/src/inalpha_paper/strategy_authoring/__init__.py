"""LLM 自创策略支持模块（D-9 · ADR-0020 E1 MVP）。

orchestrator agent 写一段完整 ``Strategy`` 子类源码 → 三道沙盒 → 跑回测 → 落候选表。

三道沙盒（必须全过）：

1. ``ast_audit`` —— AST 静态审计（import / call / name 白名单）；在 main 进程跑
2. ``contract_check`` —— 协议契约（必须继承 ``Strategy``、覆写 ``on_bar``）；
   ``dynamic_loader`` 之后跑
3. **子进程隔离** —— 复用 ``runner.run_engine_in_subprocess``；worker 入口已加 rlimit/超时

模块边界：本模块**不**直接调 DB / 不调 BacktestEngine。是纯函数 + dataclass 形态，
方便测试和复用。落库由 ``storage/strategy_candidates.py`` 负责；接入由 ``api/`` 层负责。

更详细的设计原则见 ``docs/miro/decisions/0020-strategy-evolution.md``。
"""
from .ast_audit import AuditFinding, AuditResult, audit_strategy_code
from .contract_check import ContractError, verify_strategy_contract
from .dynamic_loader import DynamicLoadError, load_strategy_class
from .fitness import FitnessInputs, calmar_from_report, compose_fitness

__all__ = [
    "AuditFinding",
    "AuditResult",
    "ContractError",
    "DynamicLoadError",
    "FitnessInputs",
    "audit_strategy_code",
    "calmar_from_report",
    "compose_fitness",
    "load_strategy_class",
    "verify_strategy_contract",
]

"""回测评估 runner —— 包装 paper 的 ``run_engine_in_subprocess``。

E1 策略：不依赖 paper 全局 ProcessPoolExecutor，每次评估启动独立子进程。
E1 预算小（4~10 次），单趟开销可控。

**注意**：multiprocessing "spawn" 模式下，子进程 lambda 和嵌套函数不可 pickle，
因此把子进程包装函数提升为模块级函数（top-level, picklable）。
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import resource
import signal
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

from inalpha_paper.runner import run_engine_in_subprocess

from ..exceptions import EvaluationError, EvaluationTimeoutError
from ..population import EvaluationResult
from .fitness import compute_fitness_from_report

logger = logging.getLogger(__name__)

# ── 模块级子进程包装函数（picklable for spawn multiprocessing） ──


def _subprocess_worker(
    fn_picklable: partial,
    pipe_conn: multiprocessing.connection.Connection,
    memory_mb: int,
    cpu_s: int,
) -> None:
    """子进程入口：设 rlimit + 执行回测 + pipe 回传。"""
    try:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (memory_mb * 1024 * 1024,) * 2,
        )
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (cpu_s, cpu_s + 20),
        )
        result = fn_picklable()
        pipe_conn.send({"ok": result})
    except Exception as exc:
        pipe_conn.send({"err": str(exc)})
    finally:
        pipe_conn.close()


@dataclass(slots=True)
class MockEvaluator:
    """测试用评估器 —— 返回预设的 canned report，不启子进程。

    用于验证 LLM 变异链路，无需 data service / paper 运行。
    """

    async def evaluate(
        self,
        source_code: str,
        universe: list[str],
        period_from: str = "2025-01-01",
        period_to: str = "2025-12-31",
        timeframe: str = "1h",
        initial_cash: float = 10000.0,
        fee_rate: float = 0.001,
    ) -> EvaluationResult:
        canned = {
            "sharpe": 1.0,
            "calmar": 0.5,
            "max_drawdown_pct": 15.0,
            "num_trades": 50,
            "num_bars_processed": 5000,
        }
        return EvaluationResult(
            report=canned,
            fitness=compute_fitness_from_report(canned, timeframe),
            data_epoch=int(datetime.now().timestamp() * 1000),
        )


@dataclass(slots=True)
class Evaluator:
    """回测评估器 —— 隔离子进程运行回测。

    E1 对每个 candidate 启动一个 ``multiprocessing.Process``，
    子进程内设 ``setrlimit`` 限制内存和 CPU 时间。
    """

    timeout_s: int = 300
    memory_mb: int = 2048
    cpu_s: int = 300

    async def evaluate(
        self,
        source_code: str,
        universe: list[str],
        period_from: str = "2025-01-01",
        period_to: str = "2025-12-31",
        timeframe: str = "1h",
        initial_cash: float = 10000.0,
        fee_rate: float = 0.001,
    ) -> EvaluationResult:
        """评估单个候选策略（E1 需要 data service 运行）。"""
        symbol = universe[0] if universe else "BTCUSDT"

        fn = partial(
            run_engine_in_subprocess,
            bars=[],  # TODO: 从 data service 拉 bars
            instrument_id=symbol,
            timeframe=timeframe,
            strategy_id=None,
            candidate_code=source_code,
            params={},
            initial_cash=initial_cash,
            fee_rate=fee_rate,
        )

        loop = asyncio.get_running_loop()
        report = await loop.run_in_executor(
            None,
            self._run_in_subprocess,
            fn,
        )

        fitness = compute_fitness_from_report(report, timeframe)
        return EvaluationResult(
            report=report,
            fitness=fitness,
            data_epoch=int(datetime.now().timestamp() * 1000),
        )

    def _run_in_subprocess(self, fn: partial) -> dict[str, Any]:
        """在子进程中执行回测函数，带资源限制。"""
        ctx = multiprocessing.get_context("spawn")

        parent_conn, child_conn = multiprocessing.Pipe()

        p = ctx.Process(
            target=_subprocess_worker,
            args=(fn, child_conn, self.memory_mb, self.cpu_s),
        )
        p.start()
        p.join(timeout=self.timeout_s)

        if p.is_alive():
            p.kill()
            p.join()
            raise EvaluationTimeoutError(
                f"回测子进程超时 ({self.timeout_s}s)"
            )

        if not parent_conn.poll(timeout=5):
            raise EvaluationError("子进程未返回结果（可能被 OOM 杀死）")

        status = parent_conn.recv()
        parent_conn.close()

        if "err" in status:
            raise EvaluationError(f"回测失败：{status['err']}")

        return status["ok"]
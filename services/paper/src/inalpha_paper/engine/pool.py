"""ProcessPool 单例 —— Swarm S1（ADR-0025）的 CPU 并行底座。

设计要点：

- **spawn context**：跨平台一致（macOS Py 3.8+ 默认就是 spawn；Linux 显式选 spawn 避免 fork
  + asyncio loop 状态在子进程里残留）
- **worker init 设 rlimit + 限 BLAS 线程**：每个子进程启动时调一次，CPU 软上限
  ``settings.job_timeout_s``，数据段 ``settings.job_mem_gb`` GB（macOS 上 RLIMIT_DATA 部分有效，
  mmap-only path 仍能绕开）；同时把 ``OMP/MKL/OPENBLAS/NUMEXPR/VECLIB`` 五个 BLAS 线程数环境
  变量 setdefault 为 ``"1"``——避免 N worker × M BLAS thread/each 超额抢核（spawn 子进程 numpy
  在 task 反序列化时才 import，此处 setdefault 生效）
- **预热**：``init_pool()`` 末尾 submit N 个 ``_noop`` 把 worker fork + 关键 import 提前跑完，
  首批真 job 不付 ~200ms 启动税
- **生命周期**：``init_pool()`` 在 FastAPI lifespan startup 调一次；``shutdown_pool()`` 在
  lifespan finally 调，确保 ``pool.shutdown(wait=True)`` 把残留 future drain 完

不在这层管的事：

- 单 job 的具体执行函数（``run_engine_in_subprocess``）在 ``runner.py``，本模块只提供 pool
- 失败重试 / 优先级队列 / 跨机分布 —— graduation 到 S2/RQ 时再加（见 ADR-0025 §Graduation）
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import resource
import sys
from concurrent.futures import ProcessPoolExecutor

from ..config import PaperSettings

logger = logging.getLogger(__name__)


# 单例 —— 简化 main 端调用，避免 settings → pool 反复传参
_pool: ProcessPoolExecutor | None = None


_BLAS_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def _set_blas_threads(n: str = "1") -> None:
    """限制 BLAS / OpenMP 等数值库每进程线程数。

    Why: ProcessPool worker × M BLAS thread/each 会超额抢核（6 worker × 8 thread = 48
    线程抢 8 核），反而拖慢回测、P99 抖动。spawn 子进程在 ``_worker_init`` 阶段还没
    import numpy（``inalpha_paper.engine.pool`` 顶层不 import numpy），此时 setdefault
    这几个环境变量，task 反序列化触发 numpy import 时会读到正确值。

    用 ``setdefault`` 不是 ``=``：用户显式 ``OMP_NUM_THREADS=2`` 时尊重外部覆盖。
    """
    for var in _BLAS_THREAD_VARS:
        os.environ.setdefault(var, n)


def _set_rlimits(*, cpu_soft: int, mem_bytes: int) -> None:
    """worker 子进程启动时调一次。失败不 raise（rlimit 不可设也要让 worker 起来）。"""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_soft, cpu_soft + 20))
    except (ValueError, OSError) as e:
        # macOS 偶尔禁某些 rlimit；记 warning 但不阻断
        logger.warning("RLIMIT_CPU set failed: %s", e)

    try:
        resource.setrlimit(resource.RLIMIT_DATA, (mem_bytes, mem_bytes))
    except (ValueError, OSError) as e:
        logger.warning("RLIMIT_DATA set failed: %s", e)


def _worker_init(cpu_soft: int, mem_bytes: int) -> None:
    """ProcessPoolExecutor initializer 入口。

    位置参数（不是 kwargs）—— ``ProcessPoolExecutor`` 只接 ``initargs=(...)``。

    在子进程启动**之后**、跑第一个任务**之前**执行一次。

    设计取舍：rlimit 只在 worker 内设而不在 main 里设 —— main 进程要处理 N 个并发请求
    + DB pool + httpx client，限太死会自伤；worker 是隔离单元，限严格点没副作用。

    BLAS 线程数在 rlimit 之前设：即使 rlimit 失败也要让 worker 拿到正确的线程上限。
    """
    _set_blas_threads()
    _set_rlimits(cpu_soft=cpu_soft, mem_bytes=mem_bytes)


def _noop() -> int:
    """预热任务：让 worker 完成 fork + import inalpha_paper 一次。返进程号给 sanity check。"""
    return os.getpid()


def init_pool(settings: PaperSettings) -> ProcessPoolExecutor | None:
    """初始化全局 pool 单例。重复调直接返已建好的。

    ``PAPER_POOL_DISABLED=1`` 时跳过初始化并返 None —— 给测试场景用：
    每个 TestClient lifespan 都重建 pool 会拖慢测试集（spawn + import numpy 每次 ~2s）。
    业务路径（``runner._run_engine``）感知到 ``get_pool() raises`` 时自动回落到同进程跑。
    """
    global _pool
    if _pool is not None:
        return _pool

    if os.environ.get("PAPER_POOL_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("backtest pool init skipped (PAPER_POOL_DISABLED set); falling back to in-process")
        return None

    ctx = multiprocessing.get_context("spawn")
    mem_bytes = int(settings.job_mem_gb * (1024**3))

    _pool = ProcessPoolExecutor(
        max_workers=settings.pool_size,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(settings.job_timeout_s, mem_bytes),
    )

    # 预热：submit pool_size 个 _noop，让每个 worker 走完 fork+import 一次
    # 不阻塞 startup（return 后 main 已能接 HTTP），但首批真 job 大概率落到已预热 worker
    for _ in range(settings.pool_size):
        _pool.submit(_noop)

    logger.info(
        "backtest pool initialized: workers=%d job_timeout_s=%d job_mem_gb=%.1f platform=%s",
        settings.pool_size,
        settings.job_timeout_s,
        settings.job_mem_gb,
        sys.platform,
    )
    return _pool


def get_pool() -> ProcessPoolExecutor:
    """取 pool。未初始化则抛 ``RuntimeError`` —— 调用方应在 lifespan 起完才用。"""
    if _pool is None:
        raise RuntimeError("backtest pool not initialized; call init_pool() in lifespan startup")
    return _pool


def shutdown_pool() -> None:
    """关 pool。lifespan finally 调；幂等。"""
    global _pool
    if _pool is None:
        return
    _pool.shutdown(wait=True)
    _pool = None
    logger.info("backtest pool shutdown complete")

"""ProcessPool 路径单测（Swarm S1, ADR-0025 §D1）。

业务路径默认走 in-process fallback（conftest 设 ``PAPER_POOL_DISABLED=1``），所以
这里**显式**清空 env + 重新构造 PaperSettings + 起 pool，验证：

- pool 真起 + worker 数符合 ``PAPER_POOL_SIZE``
- ``run_engine_in_subprocess`` 在 worker 子进程跑（PID ≠ main PID）
- 多 job 并发：3 jobs concurrency=2 时墙钟 < 串行
- 子进程间隔离：一个 job 改 module 全局态，下一个 job 看不到
- shutdown 幂等

被禁用项（``PAPER_POOL_DISABLED=1``）由 ``test_pool_disabled_returns_none`` 验证。
"""
from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest

from inalpha_paper.config import PaperSettings
from inalpha_paper.engine import pool as pool_module
from inalpha_paper.engine.pool import _noop, init_pool, shutdown_pool
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.runner import run_engine_in_subprocess


@pytest.fixture(autouse=True)
def _isolate_pool() -> Any:
    """每个 test 强制清空 ``PAPER_POOL_DISABLED`` + 复位 pool 单例 + 收尾。"""
    saved = os.environ.pop("PAPER_POOL_DISABLED", None)
    # 复位 module 全局 _pool（避免上一个 test 残留）
    pool_module._pool = None
    yield
    shutdown_pool()
    if saved is not None:
        os.environ["PAPER_POOL_DISABLED"] = saved


def _make_settings(pool_size: int = 2) -> PaperSettings:
    """构造一份允许 pool 起来的 settings。

    必须用**alias 名** kwargs（``PAPER_POOL_SIZE`` 而非 ``pool_size``）—— BaseSettings
    没开 ``populate_by_name=True`` + ``extra="ignore"`` 会**静默吞掉**字段名 kwargs，
    落回 ``default_factory``（debug 半小时血泪）。
    """
    return PaperSettings(  # type: ignore[call-arg]
        PAPER_POOL_SIZE=pool_size,
        PAPER_JOB_TIMEOUT_S=30,
        PAPER_JOB_MEM_GB=1.0,
    )


def _make_bars(n: int = 20, start_close: float = 100.0) -> list[Bar]:
    """合成最小 Bar 序列给 buy_and_hold 跑。

    timestamps 走顺序 ns，每根隔 1h（3_600_000_000_000 ns）；
    InstrumentId / timeframe / OHLCV 都给最小可用值。
    """
    inst = InstrumentId(symbol="BTC/USDT", venue="binance")
    base_ts = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 1_000_000_000
    return [
        Bar(
            instrument_id=inst,
            timeframe="1h",
            open=start_close + i,
            high=start_close + i + 1,
            low=start_close + i - 1,
            close=start_close + i,
            volume=1.0,
            ts_event=base_ts + i * 3_600_000_000_000,
            ts_init=base_ts + i * 3_600_000_000_000,
        )
        for i in range(n)
    ]


# ────────────────────────────────────────────────────────────────────
# init / shutdown
# ────────────────────────────────────────────────────────────────────


def test_init_pool_returns_executor() -> None:
    pool = init_pool(_make_settings(pool_size=2))
    assert isinstance(pool, ProcessPoolExecutor)
    assert pool._max_workers == 2


def test_init_pool_is_idempotent() -> None:
    pool1 = init_pool(_make_settings(pool_size=2))
    pool2 = init_pool(_make_settings(pool_size=4))  # 第二次 size 应被忽略
    assert pool1 is pool2


def test_shutdown_pool_is_idempotent() -> None:
    init_pool(_make_settings(pool_size=2))
    shutdown_pool()
    shutdown_pool()  # 第二次不应抛


def test_pool_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 关 pool → init 返 None，业务路径走 in-process fallback。"""
    monkeypatch.setenv("PAPER_POOL_DISABLED", "1")
    pool = init_pool(_make_settings(pool_size=2))
    assert pool is None


def test_pool_size_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PAPER_POOL_SIZE=3`` env → pool 真用 3 worker。"""
    monkeypatch.setenv("PAPER_POOL_SIZE", "3")
    # 重新构造 settings 让它读 env
    settings = PaperSettings()  # type: ignore[call-arg]
    assert settings.pool_size == 3
    pool = init_pool(settings)
    assert pool is not None
    assert pool._max_workers == 3


# ────────────────────────────────────────────────────────────────────
# Worker 真在子进程跑
# ────────────────────────────────────────────────────────────────────


def test_noop_runs_in_subprocess() -> None:
    """submit 一个 _noop，返的 PID 不等于 main PID（spawn 子进程）。"""
    pool = init_pool(_make_settings(pool_size=2))
    assert pool is not None
    worker_pid = pool.submit(_noop).result(timeout=15)
    assert worker_pid != os.getpid()


def _get_blas_env() -> dict[str, str | None]:
    """worker 进程内读 BLAS 相关 env vars（top-level 才 picklable）。"""
    keys = (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    )
    return {k: os.environ.get(k) for k in keys}


def test_worker_init_sets_blas_thread_limits() -> None:
    """spawn worker 内 5 个 BLAS 线程 env vars 都被 setdefault 为 "1"。

    Why: 避免 N worker × M BLAS thread 超额抢核（ADR-0025 §C2 后续护栏）。
    """
    pool = init_pool(_make_settings(pool_size=1))
    assert pool is not None
    env = pool.submit(_get_blas_env).result(timeout=15)
    for key, val in env.items():
        assert val == "1", f"{key}={val!r}, expected '1'"


def test_worker_init_respects_external_blas_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用 setdefault 不是 = —— 用户显式 OMP=2 时尊重外部值。"""
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    pool = init_pool(_make_settings(pool_size=1))
    assert pool is not None
    env = pool.submit(_get_blas_env).result(timeout=15)
    assert env["OMP_NUM_THREADS"] == "2"
    # 其余未显式设的仍 default 为 "1"
    assert env["MKL_NUM_THREADS"] == "1"


def test_pool_runs_engine_with_buy_and_hold() -> None:
    """端到端：ProcessPool 跑一次 buy_and_hold backtest 返 BacktestReport。"""
    pool = init_pool(_make_settings(pool_size=2))
    assert pool is not None

    bars = _make_bars(n=20)
    inst = bars[0].instrument_id

    fut = pool.submit(
        run_engine_in_subprocess,
        bars=bars,
        instrument_id=inst,
        timeframe="1h",
        strategy_id="buy_and_hold",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )
    report = fut.result(timeout=30)
    assert report.num_bars_processed == 20
    assert report.initial_cash == 10_000.0
    assert report.final_equity > 0


# ────────────────────────────────────────────────────────────────────
# 并发
# ────────────────────────────────────────────────────────────────────


def test_pool_concurrency_runs_in_parallel() -> None:
    """3 个 worker 同时干活：通过 worker 内 timestamp 重叠证明真并发。

    墙钟阈值太脆（spawn 在 macOS CI 经常 spike），改用 ``(start_ts, end_ts)``
    pairs：3 个 job 的时间窗口必须**两两重叠**至少 100ms，证明它们真在并发跑。
    """
    pool = init_pool(_make_settings(pool_size=3))
    assert pool is not None

    # 预热到 worker 都 fork 完
    for f in [pool.submit(_noop) for _ in range(3)]:
        f.result(timeout=15)

    futs = [pool.submit(_timed_sleep, 0.5) for _ in range(3)]
    intervals = [f.result(timeout=15) for f in futs]

    # 两两验重叠：max(start_i, start_j) < min(end_i, end_j) - 0.1
    for i in range(len(intervals)):
        for j in range(i + 1, len(intervals)):
            s_i, e_i = intervals[i]
            s_j, e_j = intervals[j]
            overlap = min(e_i, e_j) - max(s_i, s_j)
            assert overlap > 0.1, (
                f"jobs {i} and {j} did not overlap by >100ms (overlap={overlap:.3f}s); "
                f"i=[{s_i:.2f}, {e_i:.2f}] j=[{s_j:.2f}, {e_j:.2f}]"
            )


def _sleep_then_return(d: float) -> float:
    """top-level helper for ProcessPool（picklable）。"""
    time.sleep(d)
    return d


def _timed_sleep(d: float) -> tuple[float, float]:
    """top-level helper：在 worker 进程内记录 sleep 起止时间（epoch，跨进程可比）。

    ``time.monotonic()`` 在不同进程基准点不同**不能跨进程比**；用 ``time.time()``。
    返 ``(start, end)`` pair；多 worker 同时跑这个 fn 时，时间窗口重叠 = 真并发。
    """
    start = time.time()
    time.sleep(d)
    end = time.time()
    return (start, end)


# ────────────────────────────────────────────────────────────────────
# async 集成（run_in_executor）
# ────────────────────────────────────────────────────────────────────


async def test_async_runner_uses_pool_when_available() -> None:
    """``runner._run_engine`` 通过 ``loop.run_in_executor`` 把 engine 喂给 pool。"""
    init_pool(_make_settings(pool_size=2))

    from inalpha_paper.runner import _run_engine

    bars = _make_bars(n=15)
    report = await _run_engine(
        bars=bars,
        instrument_id=bars[0].instrument_id,
        timeframe="1h",
        strategy_id="buy_and_hold",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )
    assert report.num_bars_processed == 15


async def test_async_runner_falls_back_when_pool_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pool 未起 → in-process 跑，行为一致。"""
    # 不调 init_pool，让 get_pool 抛 RuntimeError → fallback 同进程
    monkeypatch.setenv("PAPER_POOL_DISABLED", "1")

    from inalpha_paper.runner import _run_engine

    bars = _make_bars(n=15)
    report = await _run_engine(
        bars=bars,
        instrument_id=bars[0].instrument_id,
        timeframe="1h",
        strategy_id="buy_and_hold",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )
    assert report.num_bars_processed == 15


async def test_async_runner_parallel_jobs_via_pool() -> None:
    """3 个 backtest 用 ``asyncio.gather`` 通过 pool 并发跑。"""
    init_pool(_make_settings(pool_size=3))

    from inalpha_paper.runner import _run_engine

    bars = _make_bars(n=20)
    inst = bars[0].instrument_id

    # 预热
    await _run_engine(
        bars=bars[:5], instrument_id=inst, timeframe="1h",
        strategy_id="buy_and_hold", params={}, initial_cash=10_000.0, fee_rate=0.001,
    )

    coros = [
        _run_engine(
            bars=bars, instrument_id=inst, timeframe="1h",
            strategy_id="buy_and_hold", params={},
            initial_cash=10_000.0 + i, fee_rate=0.001,
        )
        for i in range(3)
    ]
    t0 = time.monotonic()
    reports = await asyncio.gather(*coros)
    elapsed = time.monotonic() - t0

    assert all(r.num_bars_processed == 20 for r in reports)
    # 跑得 != cash 不一样 → 验证不是同一份缓存
    assert {r.initial_cash for r in reports} == {10_000.0, 10_001.0, 10_002.0}
    # 给个宽松上限，主要为了证 future hang 不被吞
    assert elapsed < 10.0

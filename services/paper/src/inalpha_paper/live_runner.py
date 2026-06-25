"""``LiveRunnerManager`` —— promoted candidate 按行情 bar 自动跑（D-11 issue #1）。

每个 running 的 strategy_run 一个长驻 asyncio task：按 timeframe 周期拉 fresh bar →
喂 :class:`LiveEngineSession` 的 ``on_bar`` → 拦截到的下单意图走护栏内 plan/exec 链路
落账（DB-backed RiskGuard + 一次性 token + 审计）→ 把成交回灌 session 保持持仓视图一致。

**信任边界（安全相关）**：每笔单走 plan/exec 但 ``approved_by='system:live_runner'``
（机器自动审批）。正当性靠上游两道人工闸门：(1) candidate 必先被人 promote；
(2) start_strategy 必由人显式调。即"人批准了这个策略上模拟盘"，之后按行情自动下单是
预期行为（ADR-0020 精神）。绝不能从这里给 LLM / 自动化开"无人 promote 也能自动下单"的路径。

**后台服务身份**：loop 调 data ``/bars`` 需 JWT，但后台无用户请求转发 token——用共享
``JWT_SECRET`` 自签一个短期 service token（sub = 账户 UUID）。market data 不挑用户身份。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import jwt
from inalpha_shared.db import get_conn
from inalpha_shared.errors import ConflictError, InalphaError

from .config import PaperSettings
from .data_client import DataClient
from .engine.live_session import LiveEngineSession
from .execution import risk_guard as risk_guard_mod
from .execution.currency_resolver import resolve_currency
from .execution.order_executor import OrderExecutor
from .execution.risk_guard_factory import RiskGuardFactory
from .execution.spot_guard import (
    InsufficientPositionError,
    violates_spot_long_only,
)
from .factor_patrol import capture_factor_baseline
from .fills import apply_fill_to_positions_and_cash
from .fx import BaseCurrencyConverter, needs_network
from .kernel.identifiers import InstrumentId, StrategyId
from .model.data import Bar
from .model.orders import Order, is_protective_order
from .runner import _bar_from_dict
from .storage import accounts as accounts_store
from .storage import closed_trades as closed_trades_store
from .storage import orders as orders_store
from .storage import positions as positions_store
from .storage import strategy_candidates as candidates_store
from .storage import strategy_runs as runs_store
from .storage import trade_plans as plans_store
from .strategy_authoring.ast_audit import audit_strategy_code
from .strategy_authoring.contract_check import verify_strategy_contract
from .strategy_authoring.dynamic_loader import load_strategy_class

_logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """区分可重试（瞬时）与不可重试（确定性）错误，决定 ``_run_loop`` 退避还是立即 errored。

    - ``InalphaError`` 且 4xx（客户端 / 约束类：校验失败 / 状态冲突 / symbol 非法等）→
      **不可重试**：重试也是同样结果，立即 errored 省退避 + 噪音（issue #37.3）
    - 其余（网络 / 超时 / DB 瞬时错 / 未知异常）→ 可重试（保守默认，避免误杀偶发错）

    注意：风控拒单（``ConflictError 409``）在 ``_route_through_plan_exec`` 内已被消化为
    risk_rejected 决策行、**不冒泡到 loop**，故不会因 409 误把整个 run 杀掉。
    """
    if isinstance(exc, InalphaError):
        return not (400 <= exc.status_code < 500)
    return True


def _classify_build_error(exc: BaseException) -> tuple[str, bool]:
    """build 阶段错误分类 → ``(reason_code, retryable)``，决定退避重试还是立即 errored（issue #41）。

    - ``InalphaError`` 4xx（确定性：candidate 校验 / symbol 非法等）→ ``strategy_error`` 不重试
    - ``InalphaError`` 5xx（含 ``DataServiceError`` status 502：data 不可达 / data 5xx）→
      ``infra_unavailable`` 重试
    - ``RuntimeError``（``_build_session`` 的 candidate 缺失 / 未 promote / AST / 契约错）→
      ``strategy_error`` 不重试
    - 其它（DB 瞬时错等未知）→ ``unknown``，保守重试
    """
    if isinstance(exc, InalphaError):
        if 400 <= exc.status_code < 500:
            return "strategy_error", False
        return "infra_unavailable", True
    if isinstance(exc, RuntimeError):
        return "strategy_error", False
    return "unknown", True


_FEE_RATE = 0.001
_LIVE_INITIAL_CASH = 10_000.0  # session 内部持仓视图用；真实现金在 DB 账户
_LIVE_RUNNER_APPROVER = "system:live_runner"
_PLAN_EXPIRE_S = 300

# timeframe → 秒（轮询周期 + backfill 回看窗口推导）
# 缺键会 fallback 1h（_timeframe_seconds），让 _closed_bars 把"开盘超 1h 的未收盘周线
# bar"误判成已收盘 → 对半成品 bar 真下单。1wk/1w 必须显式列出（issue O-1）。
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1wk": 604800, "1w": 604800,
}


def _timeframe_seconds(timeframe: str) -> int:
    return _TIMEFRAME_SECONDS.get(timeframe, 3600)


class LiveRunnerManager:
    """进程内单例：管理所有 live run 的后台 task。lifespan 起、shutdown 停。"""

    def __init__(
        self,
        *,
        risk_guard_factory: RiskGuardFactory | None,
        settings: PaperSettings,
    ) -> None:
        self._factory = risk_guard_factory
        self._settings = settings
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    # ─── 生命周期 ───

    def start(self, run: dict[str, Any]) -> None:
        """给一个 running 的 strategy_run 起后台 task。"""
        run_id: UUID = run["id"]
        if run_id in self._tasks and not self._tasks[run_id].done():
            return
        task = asyncio.create_task(self._run_loop(run), name=f"live-run-{run_id}")
        self._tasks[run_id] = task
        # task 退出（return / errored / cancel）后从 dict 移除，避免长跑实例里
        # errored / 已停的 task 对象无限堆积（CR）。stop() 已 pop 时这里是 no-op。
        # 同时检查 task 异常（issue #67）：_run_loop 自己的错误处理路径也要写 DB，
        # 那一步再失败异常会逃出 loop——不查 exception 的话 run 在 DB 永卡 running。
        task.add_done_callback(lambda t, rid=run_id: self._on_task_done(t, rid))

    def _on_task_done(self, task: asyncio.Task[None], run_id: UUID) -> None:
        """done_callback：移除 task + 检查未消化异常，兜底把 run 置 errored（issue #67）。"""
        self._tasks.pop(run_id, None)
        if task.cancelled():
            return  # stop() / stop_all() 的正常取消路径
        exc = task.exception()
        if exc is None:
            return
        _logger.error("live run %s: run loop 异常退出", run_id, exc_info=exc)
        # done_callback 是同步上下文，写 DB 必须另起 task；loop 正在关闭（服务停机）时
        # create_task 抛 RuntimeError——放弃兜底，留给重启 resume reconcile（#46）。
        try:
            asyncio.get_running_loop().create_task(self._mark_loop_crashed(run_id, exc))
        except RuntimeError:
            _logger.warning(
                "live run %s: event loop 已关闭，loop_crashed 兜底跳过（重启 reconcile 收尾）", run_id
            )

    async def _mark_loop_crashed(self, run_id: UUID, exc: BaseException) -> None:
        """best-effort 把异常退出的 run 置 errored（code=loop_crashed）。

        只尝试一次、失败仅 log——这里本身就是"写 DB 失败"的兜底，再重试可能同样失败，
        循环重试反而拖住停机。仍 running 才写，避免覆盖 stop() 已写的 stopped 终态。
        """
        try:
            async with get_conn() as conn:
                current = await runs_store.get(conn, run_id)
                if current is None or current["status"] != "running":
                    return
                # 日志写入用 savepoint 隔离（同 stop() 范式）：append 再失败（多半还是
                # 同一场 DB 抖动）不能连带跳过 set_status，否则 run 又卡回 running——
                # 那正是 #67 要修的问题
                try:
                    async with conn.transaction():
                        await runs_store.append_error_log(
                            conn, run_id, f"run loop crashed: {exc}", code="loop_crashed"
                        )
                except Exception:
                    _logger.warning(
                        "live run %s: loop_crashed 错误日志写入失败（已忽略）", run_id, exc_info=True
                    )
                # only_if_status：与 stop() 的竞态守卫（PR review）——上面 get 之后的
                # await 点里 stop() 可能已写 stopped，无守卫会把它盖成 errored
                await runs_store.set_status(conn, run_id, "errored", only_if_status="running")
        except Exception:
            _logger.exception(
                "live run %s: loop_crashed 兜底写库失败（放弃，留给重启 reconcile）", run_id
            )

    async def start_async(self, run: dict[str, Any]) -> None:
        """``start`` 的 async 包装：供 FastAPI ``BackgroundTasks`` 在**响应/事务提交后**起 task。

        必须是 async——Starlette 对 async background task 在主事件循环上 await，
        ``start`` 内部的 ``asyncio.create_task`` 才有 running loop（同步 task 会被丢进
        线程池，那里没有 loop，create_task 会 RuntimeError）。
        """
        self.start(run)

    async def stop(self, run_id: UUID) -> None:
        """停一个 run：cancel task + 置 stopped。

        只在当前仍是 'running' 时才写 'stopped'——避免把已 'errored'（策略崩过、
        error_log 有记录）的终态静默覆盖成 'stopped'，否则用户看不到策略曾崩（CR）。
        """
        task = self._tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        async with get_conn() as conn:
            current = await runs_store.get(conn, run_id)
            if current is not None and current["status"] == "running":
                # 日志写入是 best-effort，失败不应阻断 set_status——否则 run 停不下来、
                # API 返回 500，用户以为停止失败（CR）。用 savepoint 隔离：append_log 抛错
                # 只回滚这一步（否则会污染整个事务，连带 set_status 也失败），再照常置 stopped。
                try:
                    async with conn.transaction():
                        await runs_store.append_log(conn, run_id, "info", "用户停止运行")
                except Exception:
                    _logger.warning("live run %s: 停止日志写入失败（已忽略）", run_id, exc_info=True)
                # only_if_status：反向竞态守卫（PR review）——本协程 get 之后的 await 点里
                # loop_crashed 兜底可能已写 errored，无守卫会把 crash 终态盖成 stopped
                await runs_store.set_status(conn, run_id, "stopped", only_if_status="running")

    async def stop_all(self) -> None:
        """服务停机：cancel 所有 task（不改 DB 状态，重启由 reconcile 处理）。"""
        # 用 list() 快照：cancel 触发的 done_callback 会 _tasks.pop，遍历中改 dict 有隐患
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    # ─── 主循环 ───

    async def _run_loop(self, run: dict[str, Any]) -> None:
        run_id: UUID = run["id"]
        # 自动化路径 fail-closed（HIGH-2）：无人值守的下单循环不应在零风控下跑。
        # factory=None = risk_engine_enabled=false 或 TOML 加载失败 → enforce() pass-through。
        # 手动 HTTP 下单 fail-open 时人还在回路里，这里没有，必须默认拒跑。
        if self._factory is None:
            if self._settings.live_runner_require_risk_guard:
                _logger.error("live run %s: 风控不可用（factory=None），fail-closed 拒绝起跑", run_id)
                async with get_conn() as conn:
                    # savepoint 隔离（同 _mark_loop_crashed / stop() 范式，PR review）：
                    # append 失败不连带跳过 set_status，否则 run 卡 running（#67 根因），下同
                    try:
                        async with conn.transaction():
                            await runs_store.append_error_log(
                                conn, run_id,
                                "风控不可用（risk_engine_enabled=false 或 risk_rules 加载失败），"
                                "live runner 默认 fail-closed 拒绝起跑；如确需无风控运行，"
                                "设 INALPHA_LIVE_RUNNER_REQUIRE_RISK_GUARD=false",
                            )
                    except Exception:
                        _logger.warning(
                            "live run %s: fail-closed 日志写入失败（已忽略）", run_id, exc_info=True
                        )
                    # only_if_status：与 stop() 竞态守卫（PR review），下同——_run_loop 全部
                    # 离开 running 的写路径同口径，后写者不覆盖对方终态
                    await runs_store.set_status(conn, run_id, "errored", only_if_status="running")
                return
            # 显式放行：留一条醒目告警，让用户知道这个 run 在零风控下跑
            _logger.warning("live run %s: 风控不可用但已显式放行，零风控运行", run_id)
            async with get_conn() as conn:
                await runs_store.append_log(
                    conn, run_id, "warn",
                    "⚠ 风控不可用且 INALPHA_LIVE_RUNNER_REQUIRE_RISK_GUARD=false，本 run 在零风控下运行",
                )
        # build 退避（issue #41）：data 服务短暂不可用不该直接判死，退避重试；策略代码 /
        # 配置确定性错（AST / 契约 / symbol 非法）则立即 errored，不浪费退避。
        build_streak = 0
        while True:
            try:
                session, warmup_ts = await self._build_session(run)
                break
            except asyncio.CancelledError:
                raise  # stop() 触发，干净退出
            except Exception as e:
                code, retryable = _classify_build_error(e)
                _logger.warning(
                    "live run %s: build failed (code=%s, retryable=%s, streak=%d): %s",
                    run_id, code, retryable, build_streak, e,
                )
                build_streak = build_streak + 1 if retryable else build_streak
                # 不可重试 或 攒够 streak → errored；可重试 → 指数退避后重试。
                if not retryable or build_streak >= self._settings.live_max_error_streak:
                    async with get_conn() as conn:
                        try:
                            async with conn.transaction():
                                await runs_store.append_error_log(
                                    conn, run_id, f"build failed: {e}", code=code
                                )
                        except Exception:
                            _logger.warning(
                                "live run %s: build 失败日志写入失败（已忽略）", run_id, exc_info=True
                            )
                        await runs_store.set_status(
                            conn, run_id, "errored", only_if_status="running"
                        )
                    return
                async with get_conn() as conn:
                    await runs_store.append_log(
                        conn, run_id, "warn", f"build retry {build_streak}: {e}", code=code
                    )
                await asyncio.sleep(min(2 ** build_streak, 60))

        # 入场因子基准（ADR-0047）：best-effort，factor 服务不可用 → 巡检自愈补拍。
        # 放 build 成功后：candidate 已确认可跑，基准时刻≈真正起跑时刻。
        await capture_factor_baseline(run, self._settings)

        # 去重边界取 DB last_bar_ts 与 warmup_ts 的**较后者**（issue M-1）：
        # resume 续跑时 warmup 已把历史喂到 warmup_ts（可能 > DB last_bar_ts），若只用 DB
        # 边界，第一根 fetch 到的 warmup_ts bar 会绕过 dedup → 对已喂过的 bar 二次 on_bar →
        # 信号重复 → spurious 订单落库。全新 run（last_bar_ts 为 None）退化为 warmup_ts，行为不变。
        # 起跑 / 恢复 —— info 级运行日志（供运行详情「运行日志」面板观测，不只记错误）。
        # best-effort：日志写入失败（连接池耗尽 / DB 抖动）绝不能逃逸 _run_loop——否则 task
        # 带异常退出，done_callback 只移除引用、不置 errored，run 永久卡 'running' 却无 task 在跑
        # （与 stop()/_process_bar 出单日志的 try/except 对齐）（CR）。
        try:
            async with get_conn() as conn:
                await runs_store.append_log(
                    conn, run_id, "info",
                    f"{'恢复运行' if run.get('last_bar_ts') else '策略起跑'}："
                    f"{run['venue']} {run['symbol']} {run['timeframe']}",
                )
        except Exception:
            _logger.warning("live run %s: 起跑日志写入失败（已忽略）", run_id, exc_info=True)

        _db_bound = run.get("last_bar_ts")
        last_bar_ts: datetime | None = (
            max(_db_bound, warmup_ts)
            if _db_bound and warmup_ts
            else _db_bound or warmup_ts
        )
        poll_s = _timeframe_seconds(run["timeframe"])
        if self._settings.live_poll_interval_s > 0:
            poll_s = self._settings.live_poll_interval_s
        poll_s = min(poll_s, 3600)
        err_streak = 0
        # TTL 兜底（issue #44）：max_runtime_s>0 时，run 自 started_at 起超时 auto-stop。
        max_runtime_s = self._settings.live_runner_max_runtime_s
        started_at: datetime | None = run.get("started_at")

        while True:
            try:
                if await self._ttl_exceeded(run_id, started_at, max_runtime_s):
                    return
                bar = await self._fetch_latest_bar(run)
                bar_dt = _ns_to_dt(bar.ts_event) if bar is not None else None
                if bar is None or (last_bar_ts is not None and bar_dt <= last_bar_ts):
                    await asyncio.sleep(poll_s)
                    continue
                circuit_break = await self._process_bar(session, run, bar)
                last_bar_ts = bar_dt
                err_streak = 0
                # 账户级风控熔断（回撤 / 连续止损 global 锁）→ auto-stop，防僵尸 run
                # （issue #44）。置 stopped（非 errored：是策略触风控上限的正常终态，
                # 不是 bug）+ error_log 记因，让人复核后再决定是否重启。
                if circuit_break and self._settings.live_runner_auto_stop_on_circuit_break:
                    _logger.warning("live run %s: 账户级风控熔断，auto-stop", run_id)
                    async with get_conn() as conn:
                        await runs_store.append_log(
                            conn, run_id, "warn",
                            "账户级风控熔断（global scope 锁：回撤 / 连续止损上限）→ auto-stop；"
                            "复核账户状态后可重新 start。设 "
                            "INALPHA_LIVE_RUNNER_AUTO_STOP_ON_CIRCUIT_BREAK=false 维持旧行为（继续跑）",
                        )
                        # only_if_status：与 stop()/_mark_loop_crashed 同口径守卫（一致性，PR review）
                        await runs_store.set_status(
                            conn, run_id, "stopped", only_if_status="running"
                        )
                    return
                await asyncio.sleep(poll_s)
            except asyncio.CancelledError:
                raise  # stop() 触发，干净退出（事务边界外，不留半个 plan）
            except Exception as e:
                retryable = _is_retryable(e)
                # 不可重试错误不计入 streak（它是确定性的，重试无意义）。
                err_streak = err_streak + 1 if retryable else err_streak
                _logger.warning(
                    "live run %s error (retryable=%s, streak=%d): %s",
                    run_id, retryable, err_streak, e,
                )
                # handler 内的 DB 调用也要兜——否则 DB 短暂不可达时异常逃出 while
                # loop，task 静默死亡、run 永久卡在 'running'（CR）。
                try:
                    async with get_conn() as conn:
                        await runs_store.append_error_log(
                            conn, run_id, f"{type(e).__name__}: {e}"
                        )
                except Exception:
                    _logger.exception("live run %s: 写 error_log 失败", run_id)
                # 不可重试（确定性）错误 → 立即 errored，跳过退避（issue #37.3）；
                # 可重试错误 → 连续 live_max_error_streak 次才 errored，否则指数退避重试。
                if not retryable or err_streak >= self._settings.live_max_error_streak:
                    try:
                        async with get_conn() as conn:
                            await runs_store.set_status(
                                conn, run_id, "errored", only_if_status="running"
                            )
                    except Exception:
                        _logger.exception("live run %s: 置 errored 失败", run_id)
                    return
                await asyncio.sleep(min(2 ** err_streak, 60))  # 指数退避，cap 60s

    async def _ttl_exceeded(
        self, run_id: UUID, started_at: datetime | None, max_runtime_s: int
    ) -> bool:
        """run 运行超过 TTL → 置 stopped + error_log，返 True 让 _run_loop 终止（issue #44）。

        ``max_runtime_s <= 0``（默认）或 ``started_at`` 缺失 → 永不超时（返 False）。与回撤
        熔断同口径置 ``stopped``（策略超时是正常终态非 bug），防策略卡死 / 无限空跑的长尾僵尸 run。
        """
        if max_runtime_s <= 0 or started_at is None:
            return False
        elapsed = (datetime.now(UTC) - started_at).total_seconds()
        if elapsed <= max_runtime_s:
            return False
        _logger.warning(
            "live run %s: 运行 %.0fs 超过 TTL %ds，auto-stop", run_id, elapsed, max_runtime_s
        )
        async with get_conn() as conn:
            await runs_store.append_log(
                conn, run_id, "warn",
                f"运行时长 {elapsed:.0f}s 超过 TTL（INALPHA_LIVE_RUNNER_MAX_RUNTIME_S="
                f"{max_runtime_s}s）→ auto-stop（防长尾僵尸 run）；复核后可重新 start。",
            )
            # only_if_status：与 stop()/_mark_loop_crashed 同口径守卫（一致性，PR review）
            await runs_store.set_status(conn, run_id, "stopped", only_if_status="running")
        return True

    async def _build_session(
        self, run: dict[str, Any]
    ) -> tuple[LiveEngineSession, datetime | None]:
        """读 candidate code → 二次审计 + 沙盒加载 → 建 session → 历史 bar 预热。

        返回 ``(session, warmup_last_bar_ts)``。预热让需要 lookback 的策略（SMA 等）
        start 后就有指标状态，不必空跑几十根实时 bar 才出第一个信号。
        """
        async with get_conn() as conn:
            candidate = await candidates_store.get_candidate(conn, run["candidate_id"])
        if candidate is None:
            raise RuntimeError(f"candidate {run['candidate_id']} not found")
        if candidate["status"] != "promoted":
            raise RuntimeError(f"candidate {run['candidate_id']} not promoted")
        code = candidate["code"]
        audit = audit_strategy_code(code)  # defense in depth（promote 时已审）
        if not audit.ok:
            raise RuntimeError(f"candidate code failed AST audit: {audit.findings}")
        strategy_cls = load_strategy_class(code)
        verify_strategy_contract(strategy_cls)
        instrument_id = InstrumentId(symbol=run["symbol"], venue=run["venue"])
        session = LiveEngineSession(
            strategy_cls=strategy_cls,
            instrument_id=instrument_id,
            timeframe=run["timeframe"],
            params=run.get("params") or {},
            initial_cash=_LIVE_INITIAL_CASH,
            fee_rate=_FEE_RATE,
            # ADR-0052：框架级持仓保护止损（与回测共用同一阈值，行为一致）
            protective_stop_loss_pct=self._settings.protective_stop_loss_pct,
            protective_take_profit_pct=self._settings.protective_take_profit_pct,
            protective_trailing_stop_pct=self._settings.protective_trailing_stop_pct,
            protective_chandelier_atr_mult=self._settings.protective_chandelier_atr_mult,
            protective_chandelier_atr_period=self._settings.protective_chandelier_atr_period,
            # perp 透传(默认 spot/1):让 session 内存 Portfolio 走永续保证金记账,与回测/HTTP 同口径
            trading_mode=run.get("trading_mode") or "spot",
            leverage=int(run.get("leverage") or 1),
        )
        warmup_ts = await self._warmup_session(session, run)
        # resume（last_bar_ts 非空 = 本 run 之前跑过）：把 DB 当前持仓灌回 session，让续跑
        # 策略知道自己有仓（issue #37.2 / #46）。全新 run（last_bar_ts=None）保持空仓起，
        # 符合"全新 live run 从无持仓开始"语义。
        if run.get("last_bar_ts") is not None:
            await self._restore_position(session, run)
        return session, warmup_ts

    async def _restore_position(
        self, session: LiveEngineSession, run: dict[str, Any]
    ) -> None:
        """从 DB 读 run 的 (account, venue, symbol) 当前持仓，灌回 session（resume 续跑）。"""
        async with get_conn() as conn:
            pos = await positions_store.get(
                conn, account_id=run["account_id"], venue=run["venue"], symbol=run["symbol"]
            )
        if pos is None:
            return
        qty = float(pos["quantity"])
        if qty == 0:
            return
        avg = float(pos["avg_open_price"])
        # ts 用 last_bar_ts（重建发生在续喂前），ns 化喂给 session 时钟
        last_bar_ts: datetime = run["last_bar_ts"]
        ts_ns = int(last_bar_ts.timestamp() * 1_000_000_000)
        session.restore_position(quantity_signed=qty, avg_price=avg, ts_event=ts_ns)
        _logger.info(
            "live run %s: resume 重建持仓 %s %s qty=%s avg=%s",
            run["id"], run["venue"], run["symbol"], qty, avg,
        )

    async def _warmup_session(
        self, session: LiveEngineSession, run: dict[str, Any]
    ) -> datetime | None:
        """拉最近 N 根历史 bar 喂策略**预热指标**（丢弃产生的下单意图，预热期不真下单）。

        返回最后一根预热 bar 的 ts；策略持仓视图保持空仓（不 confirm_fill），符合
        "全新 live run 从无持仓开始"语义。``live_warmup_bars=0`` 时跳过。
        """
        n = self._settings.live_warmup_bars
        if n <= 0:
            return None
        token = self._mint_service_token(run["account_id"])
        now = datetime.now(UTC)
        tf_s = _timeframe_seconds(run["timeframe"])
        from_ts = now - timedelta(seconds=tf_s * (n + 1))
        async with DataClient(self._settings.data_service_url, token) as dc:
            raw = await dc.get_bars(
                venue=run["venue"],
                symbol=run["symbol"],
                timeframe=run["timeframe"],
                from_ts=from_ts,
                to_ts=now,
                limit=n,
                fresh=True,
            )
        if not raw:
            return None
        instrument_id = InstrumentId(symbol=run["symbol"], venue=run["venue"])
        # 预热也只喂已收盘 bar：否则预热末尾若是未收盘那根，last_bar_ts 会把它的 ts
        # 记下，等它真收盘时（ts 不变）被主循环去重跳过 → 策略漏掉这根（HIGH-1）。
        closed = _closed_bars(raw, instrument_id, run["timeframe"], now)
        last_ts: datetime | None = None
        for bar in closed:
            session.feed_bar(bar)  # 丢弃 orders —— 预热只为建立指标状态
            session.take_unsupported_orders()  # 同样排空，避免泄漏到 start 后第一根 bar
            last_ts = _ns_to_dt(bar.ts_event)
        return last_ts

    async def _fetch_latest_bar(self, run: dict[str, Any]) -> Bar | None:
        """拉最新一根**已收盘** fresh bar；无（已收盘的）数据返 None。"""
        token = self._mint_service_token(run["account_id"])
        now = datetime.now(UTC)
        # 回看窗口 = 几个 timeframe，避免 fresh backfill 拉太多
        lookback_s = max(_timeframe_seconds(run["timeframe"]) * 5, 7200)
        from_ts = now - timedelta(seconds=lookback_s)
        async with DataClient(self._settings.data_service_url, token) as dc:
            raw = await dc.get_bars(
                venue=run["venue"],
                symbol=run["symbol"],
                timeframe=run["timeframe"],
                from_ts=from_ts,
                # limit=5：最新一根可能未收盘要丢；时间边界极端情况下可能有多根 forming，
                # 多取几根保证仍有已收盘 bar，边际数据成本可忽略（CR medium）。
                to_ts=now,
                limit=5,
                fresh=True,
            )
        if not raw:
            return None
        instrument_id = InstrumentId(symbol=run["symbol"], venue=run["venue"])
        # 只取已收盘的最新一根，绝不在未收盘 bar 上决策（HIGH-1）
        closed = _closed_bars(raw, instrument_id, run["timeframe"], now)
        return closed[-1] if closed else None

    @staticmethod
    def _intent_for(session: LiveEngineSession, order: Order) -> str:
        """按"下单前持仓方向 + side"判多空意图（CLAUDE.md §4 多空踩坑）。

        SELL 平多=close / SELL 开空=open_short；BUY 平空=close / BUY 开多=open_long。
        """
        pos = session.portfolio.position(order.instrument_id)
        cur_qty = pos.quantity if pos is not None else 0.0
        if order.side.value == "BUY":
            return "close" if cur_qty < 0 else "open_long"
        return "close" if cur_qty > 0 else "open_short"

    async def _process_bar(self, session: LiveEngineSession, run: dict[str, Any], bar: Bar) -> bool:
        """喂一根 bar → 路由本根 bar 的下单意图 → 更新进度。（可单测，不依赖轮询）。

        返回 ``True`` 表示本根 bar 触发了账户级风控熔断（global scope 锁，issue #44），
        ``_run_loop`` 据此 auto-stop 该 run。
        """
        orders = session.feed_bar(bar)
        # 不支持单型（STOP_* 等）被 gateway 守门拒掉：记一行 rejected 决策让运维可见
        # （issue #43），不下单、不计 err_streak。必须每根 bar 排空避免跨 bar 泄漏。
        unsupported = session.take_unsupported_orders()
        for order, _sid, reason in unsupported:
            _logger.warning("live run %s: 策略下单被拒（不支持单型）：%s", run["id"], reason)
            try:
                async with get_conn() as conn:
                    await self._record_decision(
                        conn, run, order, bar, outcome="rejected",
                        intent=self._intent_for(session, order), reason=reason,
                    )
            except Exception:
                _logger.exception("live run %s: 记不支持单型决策行失败", run["id"])
        # 本根 bar 有下单意图 → info 级运行日志（无信号的空 bar 不记，避免刷屏）。
        # 措辞「触发 N 个信号（待撮合）」：此处 orders 是策略意图、尚未过风控/撮合，
        # 后续可能被 risk_rejected 全拦——不写「产生 N 个下单意图」以免与最终无成交割裂（CR）。
        if orders:
            # 区分策略信号 vs 框架 guard 兜底出场（CR #88 medium）：混算会让运维误以为
            # 策略产生了信号，实则可能是 guard 触发止损/止盈。
            guard_n = sum(1 for o, _ in orders if is_protective_order(o))
            strat_n = len(orders) - guard_n
            parts = []
            if strat_n:
                parts.append(f"策略触发 {strat_n} 个信号")
            if guard_n:
                parts.append(f"框架 guard 触发 {guard_n} 个保护性出场")
            try:
                async with get_conn() as conn:
                    await runs_store.append_log(
                        conn, run["id"], "info",
                        f"bar {_ns_to_dt(bar.ts_event):%Y-%m-%d %H:%M} · "
                        f"{' + '.join(parts)}（待撮合）",
                    )
            except Exception:
                _logger.exception("live run %s: 写出单日志失败", run["id"])
        circuit_break = False
        for order, strategy_id in orders:
            try:
                outcome = await self._route_through_plan_exec(session, order, strategy_id, run, bar)
                if outcome == "circuit_break":
                    circuit_break = True
            except Exception:
                # 部分失败清理（CR medium）：order 已被 CaptureGateway 推到 ExecutionEngine
                # 的 ACCEPTED 态，但护栏链路（DB 事务等）中途抛错时 confirm_fill/reject_order
                # 都没调到 → EE 内存留孤儿单、策略以为有挂单而 portfolio 空仓，状态分叉。
                # 先 reject 清掉 EE 内存状态，再把异常抛给 _run_loop 计 err_streak。
                session.reject_order(
                    order=order, strategy_id=strategy_id,
                    reason="route_through_plan_exec failed; cleaning up EE state",
                    ts_event=bar.ts_event,
                )
                raise
        # 进度写做 best-effort（CR medium）：本根 bar 的下单意图已落账 + confirm_fill 已回灌，
        # 这些副作用**不幂等**。若 update_progress 因 DB 瞬时错误抛出，绝不能让它逃出本函数——
        # 否则 _run_loop 的内存 last_bar_ts 不前进 → 下轮重喂同一根 bar → 重复下单 / 指标污染。
        # 进度行只用于观测 / reconcile，落后一根可接受；内存 last_bar_ts 才是去重权威。
        # cumulative_pnl 从 DB 真实持仓/平仓派生（issue #45）：已实现（closed_trades）
        # + 未实现（持仓 MtM at bar.close），按 FX 折算到账户 base_currency。不再用
        # session 的固定 1 万相对值——后者重启即归零、跨币种无意义。
        #
        # M-1：DB 读 与 外部 FX HTTP **严格分两段**——绝不在持有连接池连接时发外部请求。
        # 否则非 USD 品种（EUR 股 / JPY FX）在 data 慢/超时时，并发 run 会把连接池占满
        # （持连接等 HTTP），殃及 /strategy_runs start 等所有路径。crypto/USD 本地可解析
        # 路径零网络，第二段 convert 不开 DataClient。整段 best-effort（不重喂 bar）。
        try:
            async with get_conn() as conn:
                quote_total, currency, base = await self._read_run_pnl_quote(
                    conn, run, float(bar.close)
                )
            # ↑ 连接已归还连接池；↓ FX 折算（可能 HTTP）在连接上下文**之外**
            pnl = await self._convert_run_pnl_to_base(run, quote_total, currency, base)
            async with get_conn() as conn:
                await runs_store.update_progress(
                    conn,
                    run["id"],
                    last_bar_ts=_ns_to_dt(bar.ts_event),
                    cumulative_pnl=pnl,  # None（FX 不可用）→ 只推进 last_bar_ts、留旧 pnl
                )
        except Exception:
            _logger.exception("live run %s: update_progress 失败（best-effort，不重喂 bar）", run["id"])

        return circuit_break

    async def _read_run_pnl_quote(
        self, conn: Any, run: dict[str, Any], mark_price: float
    ) -> tuple[Decimal, str, str]:
        """读 DB 算 run 累计盈亏（**计价货币**，未折算）+ 解析币种 / base（issue #45 / M-1）。

        = 已实现（``closed_trades`` 自 started_at，按 symbol scope）+ 未实现（当前持仓
        ``(mark - avg) * qty``）- 手续费（``orders`` 自 started_at，同 symbol scope）。
        手续费已在 ``fills`` 阶段从 cash 扣，但 ``close_profit_abs`` / 未实现都是**毛
        口径**不含费，不补回这个展示盈亏会让高频策略 cumulative_pnl 虚高、看起来比真实
        净值更赚（issue #45 follow-up，用户实测发现）。
        **只读 DB、不发外部请求**（FX 折算见
        :meth:`_convert_run_pnl_to_base`，在连接池连接之外做）。

        返回 ``(total_quote, currency, base_currency)``。
        """
        account_id: UUID = run["account_id"]
        venue: str = run["venue"]
        symbol: str = run["symbol"]
        started_at: datetime = run["started_at"]

        realized = await closed_trades_store.sum_realized(
            conn, account_id=account_id, venue=venue, symbol=symbol, since=started_at
        )
        fees = await orders_store.sum_fees(
            conn, account_id=account_id, venue=venue, symbol=symbol, since=started_at
        )
        pos = await positions_store.get(
            conn, account_id=account_id, venue=venue, symbol=symbol
        )
        unrealized = Decimal(0)
        currency: str | None = None
        if pos is not None and Decimal(str(pos["quantity"])) != 0:
            qty = Decimal(str(pos["quantity"]))
            avg = Decimal(str(pos["avg_open_price"]))
            unrealized = (Decimal(str(mark_price)) - avg) * qty
            currency = pos.get("currency")

        # 净盈亏 = 毛已实现 + 毛未实现 - 手续费（手续费已在 cash 扣，这里补回展示口径）
        total_quote = realized + unrealized - fees

        account = await accounts_store.get(conn, account_id)
        base = account["base_currency"] if account else accounts_store.DEFAULT_BASE_CURRENCY
        currency = currency or resolve_currency(venue, symbol, default=base)
        return total_quote, currency, base

    async def _convert_run_pnl_to_base(
        self, run: dict[str, Any], total_quote: Decimal, currency: str, base: str
    ) -> Decimal | None:
        """把计价货币盈亏折算到 base_currency（issue #45 / M-1）。

        **不持有任何 DB 连接**——可能发 data ``/fx`` HTTP。crypto-USD 等本地可解析路径
        零网络、恒成功。拿不到汇率（非 USD 折算失败）返 ``None`` → 调用方保留旧 pnl 不覆盖。
        """
        # 0 盈亏（flat 且无平仓 / 已实现与未实现相抵）→ 折算后仍是 0，直接短路：
        # 否则非 USD flat run 每根 bar 白打一次 /fx HTTP，且 FX 不可用时会返 None 把真实
        # 的 0 PnL 错留成旧值。
        if total_quote == 0:
            return Decimal(0)
        # 计价货币 == base 或本地可解析（crypto USDT→USD）→ 零网络
        if not needs_network([currency], base):
            conv = BaseCurrencyConverter(base, None)
            return await conv.convert(total_quote, currency)

        # 否则调 data /fx；拿不到 → 返 None（保留旧值，不乱猜）
        token = self._mint_service_token(run["account_id"])
        async with DataClient(self._settings.data_service_url, token) as dc:
            conv = BaseCurrencyConverter(base, dc)
            result = await conv.convert(total_quote, currency)
        if result is None and conv.warnings:
            _logger.warning(
                "live run %s: PnL FX 折算不可用，保留旧 cumulative_pnl：%s",
                run["id"], "; ".join(conv.warnings),
            )
        return result

    async def _route_through_plan_exec(
        self,
        session: LiveEngineSession,
        order: Order,
        strategy_id: StrategyId,
        run: dict[str, Any],
        bar: Bar,
    ) -> str:
        """一笔下单意图走护栏内 plan/exec：风控 → 撮合 → plan create/approve/consume → 落账 → 回灌。

        返回本笔 outcome：``"filled"`` / ``"rejected"`` / ``"risk_rejected"`` /
        ``"circuit_break"``（账户级 global 风控锁，issue #44）。
        """
        account_id: UUID = run["account_id"]
        venue: str = run["venue"]
        symbol: str = run["symbol"]
        run_id: UUID = run["id"]
        side = order.side.value  # "BUY" / "SELL"

        # intent 按"下单前持仓方向 + side"判（CLAUDE.md §4 多空踩坑）。在风控之前算好
        # → risk_rejected 决策行也带上 intent（复盘做空语义不丢）。
        intent = self._intent_for(session, order)

        # 1. 风控；命中 → 拒单 + 记 error_log，不杀 run。两道闸门同 except 处理：
        #    (a) 单笔 notional 硬上限（无状态，挡策略算错 quantity 的超大单，issue #42）；
        #    (b) DB-backed RiskGuard 行为型锁规则（drawdown / cooldown / ...）。
        # ADR-0052：框架级保护性出场（stop_loss / take_profit / trailing_stop_loss）**两道闸
        # 全豁免**——guard 是风控兜底，必须能平仓：① 行为型锁（回撤熔断锁期内恰恰最需要它平
        # 仓）；② notional 硬上限（CR #88 major：保护性 SELL 量=实际持仓，价涨/累积建仓后仓位
        # notional 必然超单笔买入上限；若也卡 notional → 止损被静默拒 → 持仓不动 → 每根 bar
        # 重试又被拒 → 止损形同虚设。notional 上限是防"胖手指"超大开仓单，平实际持仓不属此列）。
        # 三因子判定(side=SELL + tag + guard 专属 client_order_id 前缀)：策略代码可控 tag/前缀，
        # 单看 tag 会被仿冒绕过风控（CR #88 major），必须三者同时校验。
        is_protective_exit = is_protective_order(order)
        try:
            if not is_protective_exit:
                risk_guard_mod.check_order_notional(
                    self._factory, quantity=order.quantity, ref_price=float(bar.close),
                    venue=venue, symbol=symbol,
                )
                await risk_guard_mod.enforce(
                    self._factory, account_id=account_id, venue=venue, symbol=symbol,
                    side=side,
                )
        except ConflictError as e:
            session.reject_order(
                order=order, strategy_id=strategy_id,
                reason=f"RISK_REJECTED: {e.message}", ts_event=bar.ts_event,
            )
            async with get_conn() as conn:
                await runs_store.append_log(
                    conn, run_id, "warn", f"order rejected by risk: {e.message}"
                )
                await self._record_decision(
                    conn, run, order, bar, outcome="risk_rejected", intent=intent,
                    reason=e.message,
                )
            # 账户级（global scope）锁 = 回撤 / 连续止损熔断：返回信号让 _run_loop 终止 run
            # （issue #44）。symbol/market scope（cooldown 等）是局部、会过的，不熔断。
            # notional 上限（MaxOrderNotional）无 lock_scope，故 .get 返 None，不误判。
            scope = e.details.get("lock_scope") if e.details else None
            return "circuit_break" if scope == "global" else "risk_rejected"

        # 1.5 现货 long-only 网关守门（与回测 Portfolio.can_afford_sell 同口径）：
        # OrderExecutor 是无状态纯函数、撮合前不查持仓，apply_fill 又允许负仓——若不在此
        # 拦截，long-only 策略空仓时的 SELL 会被照单成交成裸空、滚出策略平不掉的空头（漂移）。
        # 读 DB 持仓（权威：apply_fill 落账的真实持仓，且能防 session/DB 视图分叉）。
        #
        # **框架保护性出场（stop_loss / trailing / take_profit）跳过本乐观快闸**：单一路径下
        # 它卖的就是真实持仓（quantity == current）自然放行；但若同账户被 HTTP /orders/submit
        # 卖过一笔（DB 实仓 < 策略视图），整单拒会让止损被静默吃掉、实仓无保护、暴露持续扩大。
        # 故保护性出场不在此提前拒，下放到下方事务内 FOR UPDATE 权威闸——按真实持仓**钳量全平**
        # （既不超卖翻空，也不丢保护）。
        if side == "SELL" and not is_protective_exit:
            async with get_conn() as conn:
                cur_pos = await positions_store.get(
                    conn, account_id=account_id, venue=venue, symbol=symbol
                )
            current_qty = Decimal(str(cur_pos["quantity"])) if cur_pos else Decimal(0)
            if violates_spot_long_only(
                side=side, quantity=order.quantity, current_qty=current_qty,
                trading_mode=run.get("trading_mode") or "spot",
            ):
                reason = (
                    f"INSUFFICIENT_POSITION: sell {order.quantity} exceeds position "
                    f"{current_qty} (spot long-only guard)"
                )
                session.reject_order(
                    order=order, strategy_id=strategy_id,
                    reason=reason, ts_event=bar.ts_event,
                )
                async with get_conn() as conn:
                    await runs_store.append_log(
                        conn, run_id, "warn", f"order rejected by spot guard: {reason}"
                    )
                    await self._record_decision(
                        conn, run, order, bar, outcome="rejected", intent=intent,
                        reason=reason,
                    )
                return "rejected"

        # 2. 撮合（纯函数，ref_price = bar.close）
        result = OrderExecutor.execute(
            venue=venue,
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            order_type=order.type.value,  # type: ignore[arg-type]
            quantity=order.quantity,
            price=order.price,
            ref_price=float(bar.close),
            fee_rate=_FEE_RATE,
        )

        # 3. 走 plan/exec 链路落账（机器自动审批）
        # exec_qty = 实际撮合 / 落账量（float，喂 OrderExecutor / order_params）。保护性出场遇
        # session/DB 分叉时会在下方事务内被钳到真实持仓（见 step 1.5 注释）。
        # clamped_fill：钳量场景下的**精确 Decimal** 成交量（= 锁内读到的 locked_qty），直接喂
        # apply_fill / 决策日志，绕开 Decimal→float→Decimal 往返——避免未来 12+ 位精度品种
        # （高精度 altcoin / 合约乘数）浮点舍入在持仓表留极小微尘仓位、误触后续守门。
        exec_qty = order.quantity
        clamped_fill: Decimal | None = None
        order_params = {
            "side": side, "type": order.type.value,
            "quantity": exec_qty, "price": order.price,
        }
        rationale = (
            f"[live_runner run:{run_id}] candidate:{run['candidate_id']} on_bar {side} signal"
        )
        try:
            async with get_conn() as conn, conn.transaction():
                await accounts_store.get_or_create(conn, account_id)  # 首单 lazy create 账户
                # 现货 long-only 权威守门（事务内 FOR UPDATE）：闭合 step 1.5 乐观读与本 apply
                # 跨事务的 TOCTOU——并发同账户同标的 SELL 各读旧持仓双双过 step 1.5，这里锁行
                # 串行化，第二个读到更新后持仓 → raise 回滚（不落 plan/order/fill）转 except 拒单。
                if side == "SELL" and result["status"] == "FILLED":
                    locked = await positions_store.get(
                        conn, account_id=account_id, venue=venue, symbol=symbol,
                        for_update=True,
                    )
                    locked_qty = Decimal(str(locked["quantity"])) if locked else Decimal(0)
                    if violates_spot_long_only(
                        side=side, quantity=exec_qty, current_qty=locked_qty,
                        trading_mode=run.get("trading_mode") or "spot",
                    ):
                        if is_protective_exit and locked_qty > 0:
                            # 框架保护性出场遇 session/DB 视图分叉（DB 实仓 < 策略视图，例如
                            # 同账户被 HTTP /orders/submit 卖过一笔）：钳到 DB 实仓**全平**——
                            # 既不超卖翻空（裸空），也不整单拒（拒则实仓无保护、暴露持续扩大，
                            # 即 #109 CR medium）。用钳后量在锁内重算 fill，下方 insert /
                            # apply_fill / plan 全部用 exec_qty / result 同口径。
                            exec_qty = float(locked_qty)
                            clamped_fill = locked_qty  # 精确 Decimal,绕开 float 往返
                            order_params["quantity"] = exec_qty
                            result = OrderExecutor.execute(
                                venue=venue, symbol=symbol,
                                side=side,  # type: ignore[arg-type]
                                order_type=order.type.value,  # type: ignore[arg-type]
                                quantity=exec_qty, price=order.price,
                                ref_price=float(bar.close), fee_rate=_FEE_RATE,
                            )
                        else:
                            raise InsufficientPositionError(
                                f"INSUFFICIENT_POSITION: sell {exec_qty} exceeds position "
                                f"{locked_qty} (spot long-only guard)"
                            )
                plan = await plans_store.create(
                    conn, account_id=account_id, intent=intent, venue=venue, symbol=symbol,
                    order_params=order_params, rationale=rationale,
                    expire_in_seconds=_PLAN_EXPIRE_S,
                )
                plan_id = plan["plan_id"]
                approved = await plans_store.approve(
                    conn, account_id=account_id, plan_id=plan_id, approver=_LIVE_RUNNER_APPROVER
                )
                await plans_store.consume_approval(
                    conn, account_id=account_id, plan_id=plan_id,
                    approval_token=approved["approval_token"],
                )
                await orders_store.insert(
                    conn, account_id=account_id, client_order_id=result["client_order_id"],
                    venue=venue, symbol=symbol, side=side, order_type=order.type.value,
                    quantity=exec_qty, price=order.price, status=result["status"],
                    filled_quantity=result["filled_quantity"],
                    avg_fill_price=result["avg_fill_price"], fee=result["fee"],
                    notional=result["notional"], ts_event=result["ts_event"],
                    trade_plan_id=plan_id,
                    trading_mode=run.get("trading_mode") or "spot",
                    leverage=int(run.get("leverage") or 1),
                )
                if result["status"] == "FILLED":
                    fill_qty_decimal = (
                        clamped_fill if clamped_fill is not None
                        else Decimal(str(result["filled_quantity"]))
                    )
                    realized_pnl = await apply_fill_to_positions_and_cash(
                        conn, account_id=account_id, venue=venue, symbol=symbol, side=side,
                        quantity=fill_qty_decimal,
                        fill_price=Decimal(str(result["avg_fill_price"])),
                        fee=Decimal(str(result["fee"])),
                        ts_event=result["ts_event"], order_id=result["client_order_id"],
                        trading_mode=run.get("trading_mode") or "spot",
                        leverage=int(run.get("leverage") or 1),
                    )
                    # 回写该笔已实现盈亏(开仓 0 / 平仓实现值)——与 api/orders 提交路径
                    # 同口径;漏写会让控制台「最近订单」的本笔盈亏恒为空。
                    await orders_store.set_realized_pnl(
                        conn,
                        client_order_id=result["client_order_id"],
                        realized_pnl=realized_pnl,
                    )
                await plans_store.record_execution(
                    conn, plan_id=plan_id, resulting_order_id=result["client_order_id"]
                )
                # 决策复盘日志（与订单同事务，原子）
                filled = result["status"] == "FILLED"
                await self._record_decision(
                    conn, run, order, bar,
                    outcome="filled" if filled else "rejected",
                    intent=intent,
                    plan_id=plan_id,
                    order_id=str(result["client_order_id"]),
                    fill_price=Decimal(str(result["avg_fill_price"])) if filled else None,
                    fee=Decimal(str(result["fee"])) if filled else None,
                    reason=None if filled else str(result.get("rejection_reason") or "not filled"),
                    # 钳量分支 = 精确 locked_qty（与 orders 落账 / apply_fill 同源）；
                    # 普通 / rejected 路径 = 意图量 exec_qty。
                    quantity=clamped_fill if clamped_fill is not None else Decimal(str(exec_qty)),
                )
        except InsufficientPositionError as e:
            # 事务内 FOR UPDATE 守门命中并发竞态：事务已回滚（无 plan/order/fill），
            # 补 session 拒单 + 决策日志（新连接，原事务已废）。
            session.reject_order(
                order=order, strategy_id=strategy_id,
                reason=e.message, ts_event=bar.ts_event,
            )
            async with get_conn() as conn:
                await runs_store.append_log(
                    conn, run_id, "warn",
                    f"order rejected by spot guard (txn race): {e.message}",
                )
                await self._record_decision(
                    conn, run, order, bar, outcome="rejected", intent=intent,
                    reason=e.message,
                )
            return "rejected"

        # 4. 回灌 session：成交更新 portfolio + 策略持仓视图；未成交清理 ExecutionEngine 状态
        # 已知残差（保护性钳量场景）：confirm_fill 按钳后量（如 0.5）增量减仓，DB 已被钳到全平
        # （0），故 session 视图残留分叉（session 0.5 vs DB 0；前置分叉源自 HTTP 卖单）。注意
        # PositionGuard 首次触发时已把该 inst 记入 _pending_exit_insts，而 confirm_fill 不清它
        # （只有 reject_order 的保护性单分支、或 pos 转 flat 才清，见 position_guard.evaluate）——
        # session 残仓非 flat → 后续 bar guard.evaluate 命中 pending 去重 → **对该 inst 静默 skip、
        # 不再发出场单**（不是反复 rejected 噪音）。此刻 DB 实仓已 0、无即时暴露；但若策略此后在该
        # inst 重新建仓，guard 因 pending 未清仍跳过 → 新仓得不到 guard 保护。这属 session/DB 对账
        # 缺口（restart 或 _restore_position reconcile 可一并清 session + pending），不在本「止损不再
        # 被静默吃掉」修复范围内，留作 follow-up。
        if result["status"] == "FILLED":
            session.confirm_fill(
                order=order, strategy_id=strategy_id,
                fill_qty=float(result["filled_quantity"]),
                fill_price=float(result["avg_fill_price"]),
                ts_event=bar.ts_event,
            )
            return "filled"
        session.reject_order(
            order=order, strategy_id=strategy_id,
            reason=str(result.get("rejection_reason") or "not filled"),
            ts_event=bar.ts_event,
        )
        return "rejected"

    async def _record_decision(
        self,
        conn: Any,
        run: dict[str, Any],
        order: Order,
        bar: Bar,
        *,
        outcome: str,
        intent: str | None = None,
        plan_id: UUID | None = None,
        order_id: str | None = None,
        fill_price: Decimal | None = None,
        fee: Decimal | None = None,
        reason: str | None = None,
        quantity: Decimal | None = None,
    ) -> None:
        """记一行决策复盘日志（策略在某根 bar 的下单意图 + 撮合结果）。

        ``quantity`` 缺省记 ``order.quantity``（策略意图量）；保护性出场钳量分叉时显式传
        钳后量（``exec_qty``），让决策行的 quantity 与 ``orders`` 表落账量一致，避免复盘
        面板显示「SELL 1.0 filled」而落账实为 0.5 的对不上。
        """
        await runs_store.insert_decision(
            conn,
            run_id=run["id"],
            bar_ts=_ns_to_dt(bar.ts_event),
            bar_close=Decimal(str(bar.close)),
            side=order.side.value,
            quantity=quantity if quantity is not None else Decimal(str(order.quantity)),
            order_type=order.type.value,
            limit_price=Decimal(str(order.price)) if order.price is not None else None,
            tag=order.tag,  # 策略可经 Order.tag 透传语义意图（stop_loss / take_profit / ...）
            intent=intent,  # open_long / open_short / close（补 side 缺失的多空语义）
            outcome=outcome,
            fill_price=fill_price,
            fee=fee,
            plan_id=plan_id,
            order_id=order_id,
            reason=reason,
        )

    # ─── 工具 ───

    def _mint_service_token(self, account_id: UUID) -> str:
        """自签短期 service JWT（sub = 账户 UUID）调 data /bars。共享密钥，与 verify_jwt 对称。"""
        payload = {
            "sub": str(account_id),
            "exp": int(time.time()) + self._settings.live_runner_token_ttl_s,
        }
        return jwt.encode(
            payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm
        )


def _ns_to_dt(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)


def _closed_bars(
    raw: list[dict[str, Any]],
    instrument_id: InstrumentId,
    timeframe: str,
    now: datetime,
) -> list[Bar]:
    """从 data /bars 原始返回里只挑**已收盘**的 bar（HIGH-1）。

    OHLCV 的 ts 是 K 线**开盘时刻**；最新一根常是当前**未收盘**那根（ccxt 多数交易所
    默认会带）。在未收盘 bar 上决策 = 拿临时 close 下单，且 ts 不变会被去重永不复评 →
    幻影信号。判据：``open_ts + timeframe <= now`` 才算收盘。时钟略有偏差时宁可保守跳过
    （下一轮再处理），也不在半根 bar 上交易。
    """
    tf_s = _timeframe_seconds(timeframe)
    cutoff = now - timedelta(seconds=tf_s)  # open_ts <= cutoff ⟺ open_ts + tf <= now
    out: list[Bar] = []
    for b in raw:
        bar = _bar_from_dict(b, instrument_id, timeframe)
        if _ns_to_dt(bar.ts_event) <= cutoff:
            out.append(bar)
    return out

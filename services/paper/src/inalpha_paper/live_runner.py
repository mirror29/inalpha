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
from inalpha_shared.errors import ConflictError

from .config import PaperSettings
from .data_client import DataClient
from .engine.live_session import LiveEngineSession
from .execution import risk_guard as risk_guard_mod
from .execution.order_executor import OrderExecutor
from .execution.risk_guard_factory import RiskGuardFactory
from .fills import apply_fill_to_positions_and_cash
from .kernel.identifiers import InstrumentId, StrategyId
from .model.data import Bar
from .model.orders import Order
from .runner import _bar_from_dict
from .storage import accounts as accounts_store
from .storage import orders as orders_store
from .storage import strategy_candidates as candidates_store
from .storage import strategy_runs as runs_store
from .storage import trade_plans as plans_store
from .strategy_authoring.ast_audit import audit_strategy_code
from .strategy_authoring.contract_check import verify_strategy_contract
from .strategy_authoring.dynamic_loader import load_strategy_class

_logger = logging.getLogger(__name__)

_FEE_RATE = 0.001
_LIVE_INITIAL_CASH = 10_000.0  # session 内部持仓视图用；真实现金在 DB 账户
_LIVE_RUNNER_APPROVER = "system:live_runner"
_PLAN_EXPIRE_S = 300

# timeframe → 秒（轮询周期 + backfill 回看窗口推导）
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400,
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

    async def stop(self, run_id: UUID) -> None:
        """停一个 run：cancel task + 置 stopped。"""
        task = self._tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        async with get_conn() as conn:
            await runs_store.set_status(conn, run_id, "stopped")

    async def stop_all(self) -> None:
        """服务停机：cancel 所有 task（不改 DB 状态，重启由 reconcile 处理）。"""
        for task in self._tasks.values():
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
        try:
            session = await self._build_session(run)
        except Exception as e:
            _logger.exception("live run %s: session build failed", run_id)
            async with get_conn() as conn:
                await runs_store.append_error_log(conn, run_id, f"build failed: {e}")
                await runs_store.set_status(conn, run_id, "errored")
            return

        last_bar_ts: datetime | None = run.get("last_bar_ts")
        poll_s = _timeframe_seconds(run["timeframe"])
        if self._settings.live_poll_interval_s > 0:
            poll_s = self._settings.live_poll_interval_s
        poll_s = min(poll_s, 3600)
        err_streak = 0

        while True:
            try:
                bar = await self._fetch_latest_bar(run)
                bar_dt = _ns_to_dt(bar.ts_event) if bar is not None else None
                if bar is None or (last_bar_ts is not None and bar_dt <= last_bar_ts):
                    await asyncio.sleep(poll_s)
                    continue
                await self._process_bar(session, run, bar)
                last_bar_ts = bar_dt
                err_streak = 0
                await asyncio.sleep(poll_s)
            except asyncio.CancelledError:
                raise  # stop() 触发，干净退出（事务边界外，不留半个 plan）
            except Exception as e:
                err_streak += 1
                _logger.warning("live run %s error (streak=%d): %s", run_id, err_streak, e)
                async with get_conn() as conn:
                    await runs_store.append_error_log(conn, run_id, f"{type(e).__name__}: {e}")
                if err_streak >= self._settings.live_max_error_streak:
                    async with get_conn() as conn:
                        await runs_store.set_status(conn, run_id, "errored")
                    return
                await asyncio.sleep(min(2 ** err_streak, 60))  # 指数退避，cap 60s

    async def _build_session(self, run: dict[str, Any]) -> LiveEngineSession:
        """读 candidate code → 二次审计 + 沙盒加载 → 建 session。"""
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
        return LiveEngineSession(
            strategy_cls=strategy_cls,
            instrument_id=instrument_id,
            timeframe=run["timeframe"],
            params=run.get("params") or {},
            initial_cash=_LIVE_INITIAL_CASH,
            fee_rate=_FEE_RATE,
        )

    async def _fetch_latest_bar(self, run: dict[str, Any]) -> Bar | None:
        """拉最新一根 fresh bar；无数据返 None。"""
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
                to_ts=now,
                limit=2,
                fresh=True,
            )
        if not raw:
            return None
        instrument_id = InstrumentId(symbol=run["symbol"], venue=run["venue"])
        return _bar_from_dict(raw[-1], instrument_id, run["timeframe"])

    async def _process_bar(self, session: LiveEngineSession, run: dict[str, Any], bar: Bar) -> None:
        """喂一根 bar → 路由本根 bar 的下单意图 → 更新进度。（可单测，不依赖轮询）。"""
        orders = session.feed_bar(bar)
        for order, strategy_id in orders:
            await self._route_through_plan_exec(session, order, strategy_id, run, bar)
        async with get_conn() as conn:
            await runs_store.update_progress(
                conn,
                run["id"],
                last_bar_ts=_ns_to_dt(bar.ts_event),
                cumulative_pnl=Decimal(str(session.cumulative_pnl())),
            )

    async def _route_through_plan_exec(
        self,
        session: LiveEngineSession,
        order: Order,
        strategy_id: StrategyId,
        run: dict[str, Any],
        bar: Bar,
    ) -> None:
        """一笔下单意图走护栏内 plan/exec：风控 → 撮合 → plan create/approve/consume → 落账 → 回灌。"""
        account_id: UUID = run["account_id"]
        venue: str = run["venue"]
        symbol: str = run["symbol"]
        run_id: UUID = run["id"]
        side = order.side.value  # "BUY" / "SELL"

        # 1. 风控（DB-backed RiskGuard）；命中 → 拒单 + 记 error_log，不杀 run
        try:
            await risk_guard_mod.enforce(
                self._factory, account_id=account_id, venue=venue, symbol=symbol, side=side
            )
        except ConflictError as e:
            session.reject_order(
                order=order, strategy_id=strategy_id,
                reason=f"RISK_REJECTED: {e.message}", ts_event=bar.ts_event,
            )
            async with get_conn() as conn:
                await runs_store.append_error_log(
                    conn, run_id, f"order rejected by risk: {e.message}"
                )
            return

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
        order_params = {
            "side": side, "type": order.type.value,
            "quantity": order.quantity, "price": order.price,
        }
        intent = "open_long" if side == "BUY" else "close"
        rationale = (
            f"[live_runner run:{run_id}] candidate:{run['candidate_id']} on_bar signal"
        )
        async with get_conn() as conn, conn.transaction():
            await accounts_store.get_or_create(conn, account_id)  # 首单 lazy create 账户
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
                quantity=order.quantity, price=order.price, status=result["status"],
                filled_quantity=result["filled_quantity"],
                avg_fill_price=result["avg_fill_price"], fee=result["fee"],
                notional=result["notional"], ts_event=result["ts_event"],
                trade_plan_id=plan_id,
            )
            if result["status"] == "FILLED":
                await apply_fill_to_positions_and_cash(
                    conn, account_id=account_id, venue=venue, symbol=symbol, side=side,
                    quantity=Decimal(str(result["filled_quantity"])),
                    fill_price=Decimal(str(result["avg_fill_price"])),
                    fee=Decimal(str(result["fee"])),
                    ts_event=result["ts_event"], order_id=result["client_order_id"],
                )
            await plans_store.record_execution(
                conn, plan_id=plan_id, resulting_order_id=result["client_order_id"]
            )

        # 4. 回灌 session：成交更新 portfolio + 策略持仓视图；未成交清理 ExecutionEngine 状态
        if result["status"] == "FILLED":
            session.confirm_fill(
                order=order, strategy_id=strategy_id,
                fill_qty=float(result["filled_quantity"]),
                fill_price=float(result["avg_fill_price"]),
                ts_event=bar.ts_event,
            )
        else:
            session.reject_order(
                order=order, strategy_id=strategy_id,
                reason=str(result.get("rejection_reason") or "not filled"),
                ts_event=bar.ts_event,
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

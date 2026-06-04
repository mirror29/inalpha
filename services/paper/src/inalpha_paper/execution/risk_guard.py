"""``RiskGuard`` —— HTTP 路径的执行层风控（async + PG 持久化）。

与 [`RiskEngine`](./risk_engine.py) 的关系：

- ``RiskEngine`` 是 backtest 路径专用：sync + msgbus 驱动 + ``InMemoryLockStore``
- ``RiskGuard`` 是 HTTP 路径专用：async + 直接 await + PG ``risk_locks`` 表
- 两者**共享** ``RiskRule`` 5 件套 + 同一份 ``risk_rules.toml`` 配置
- 锁是 venue-scoped 隔离（HTTP 写实时锁，backtest 跑虚拟时间不会读到，反之亦然）

调用约定：
- FastAPI route 调 ``await risk_guard.check(conn, instrument_id=..., side=..., now=...)``
- 拦截返 :class:`RiskRejection`；通过返 ``None``
- 拦截时**已经**把 lock 写入 ``risk_locks`` 表（route 拿到后只需要抛 409）

ADR-0006 §D3 设计原文是"PostgreSQL 持久化走异步路径（``storage/risk_locks.py``），
由后台 reconcile worker 定期把 InMemory state dump 进 DB"；本模块改成 HTTP 路径
**直接读写 PG**——不再依赖 reconcile worker，简化跨进程 / 重启场景。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from inalpha_shared.db import get_conn
from inalpha_shared.errors import ConflictError
from psycopg import AsyncConnection

from ..kernel.identifiers import InstrumentId
from ..storage import risk_locks as locks_store
from .risk_rules import RiskRule
from .risk_rules.base import Side
from .risk_rules.exchange_resolver import resolve_calendar_code

if TYPE_CHECKING:
    from .risk_guard_factory import RiskGuardFactory


@dataclass(frozen=True, slots=True)
class RiskRejection:
    """RiskGuard.check 命中后的统一返回。

    HTTP route 拿到非 ``None`` 直接抛 409 ``RISK_REJECTED``。
    """

    rule_name: str
    reason: str
    locked_until: datetime
    lock_scope: str
    """``global`` / ``market`` / ``symbol``。"""

    from_existing_lock: bool
    """True = 命中现有锁；False = 本次新触发并写入 DB。"""


class RiskGuard:
    """HTTP 路径的执行层风控（参考 [`RiskEngine`](./risk_engine.py) 设计）。

    构造：
        rules: 已实例化的规则列表（用 ``build_rules`` 从 TOML 配置生成）
        starting_balance: 账户起始余额（喂给 ``check_global`` 算 drawdown）

    `rules` 为空 → ``check`` 直接返 ``None``（pass-through，与 RiskEngine 一致）。
    """

    def __init__(
        self,
        *,
        rules: list[RiskRule],
        starting_balance: float = 10_000.0,
    ) -> None:
        self._rules = list(rules)
        self._starting_balance = starting_balance

    @property
    def rule_names(self) -> list[str]:
        return [r.name for r in self._rules]

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    async def check(
        self,
        conn: AsyncConnection,
        *,
        instrument_id: InstrumentId,
        side: Side,
        now: datetime,
    ) -> RiskRejection | None:
        """风控前置闸门。

        流程（同 ``RiskEngine._check_and_maybe_reject``）：

        1. 按 ``global → market → symbol`` 顺序查 ``risk_locks`` 表
           的现有 active 锁；命中即返 ``RiskRejection(from_existing_lock=True)``
        2. 没现有锁 → 按同样顺序跑各层 ``check_*``；命中即写入 ``risk_locks`` +
           返 ``RiskRejection(from_existing_lock=False)``
        3. 都通过 → 返 ``None``
        """
        if not self._rules:
            return None

        symbol_str = str(instrument_id)
        # market 锁键用交易所日历 code（同交易所共享开闭市），无法解析时 fallback venue
        market_key = (
            resolve_calendar_code(instrument_id.venue, instrument_id.symbol)
            or instrument_id.venue
        )

        # 1. 现有锁
        for scope, kwargs in (
            ("global", {}),
            ("market", {"market": market_key}),
            ("symbol", {"symbol": symbol_str}),
        ):
            existing = await locks_store.is_locked(
                conn, now=now, scope=scope, side=side, **kwargs
            )
            if existing is not None:
                return _rejection_from_lock_row(existing)

        # 2. 跑 rules（rule check_* 是 sync 的，直接调）
        balance = self._starting_balance

        for rule in self._rules:
            if rule.has_global_check:
                verdict = rule.check_global(now, side, balance)
                if verdict is not None:
                    return await _record_and_build_rejection(
                        conn, verdict, instrument_id=None, now=now
                    )

        for rule in self._rules:
            if rule.has_market_check:
                verdict = rule.check_market(instrument_id, now, side, balance)
                if verdict is not None:
                    return await _record_and_build_rejection(
                        conn, verdict, instrument_id=None, now=now
                    )

        for rule in self._rules:
            if rule.has_symbol_check:
                verdict = rule.check_symbol(instrument_id, now, side, balance)
                if verdict is not None:
                    return await _record_and_build_rejection(
                        conn, verdict, instrument_id=instrument_id, now=now
                    )

        return None


def _rejection_from_lock_row(row: dict) -> RiskRejection:  # type: ignore[type-arg]
    return RiskRejection(
        rule_name=row["rule_name"],
        reason=f"[{row['rule_name']}] {row['reason']}（已锁，至 {row['locked_until'].isoformat()}）",
        locked_until=row["locked_until"],
        lock_scope=row["scope"],
        from_existing_lock=True,
    )


async def _record_and_build_rejection(
    conn: AsyncConnection,
    verdict,  # RiskVerdict, 避免顶层 import 形成循环
    *,
    instrument_id: InstrumentId | None,
    now: datetime,
) -> RiskRejection:
    """命中 rule → 写 ``risk_locks`` + 返 RiskRejection。"""
    market = verdict.lock_market
    symbol: str | None = None
    if verdict.lock_scope == "symbol" and instrument_id is not None:
        symbol = str(instrument_id)
        if market is None:
            market = instrument_id.venue

    await locks_store.insert(
        conn,
        scope=verdict.lock_scope,
        rule_name=verdict.rule_name,
        reason=verdict.reason,
        locked_until=verdict.until,
        market=market,
        symbol=symbol,
        side=verdict.lock_side,
    )
    return RiskRejection(
        rule_name=verdict.rule_name,
        reason=f"[{verdict.rule_name}] {verdict.reason}",
        locked_until=verdict.until,
        lock_scope=verdict.lock_scope,
        from_existing_lock=False,
    )


# ────────────────────────────────────────────────────────────────────
# HTTP layer helper —— api/orders.py + api/trade_plans.py 共用
# ────────────────────────────────────────────────────────────────────


def _http_side_to_rule_side(side: str) -> Side:
    """HTTP ``'BUY'`` / ``'SELL'`` → rule ``'long'`` / ``'short'``。"""
    return "long" if side == "BUY" else "short"


def check_order_notional(
    factory: RiskGuardFactory | None,
    *,
    quantity: float,
    ref_price: float,
    venue: str,
    symbol: str,
) -> None:
    """单笔名义价值硬上限——无状态前置校验（issue #42）。

    与 :func:`enforce` 的行为型锁规则**正交**：notional 上限是 per-order、stateless 的
    "防胖手指 / 防策略算错 quantity" 闸门，超限只拒**这一笔**，**不写锁**（下一笔合规
    小单应当能过；锁会误伤）。HTTP 手动下单有人盯着兜底，但 live runner 无人值守按 bar
    自动下单——一个算出 ``quantity=1e9`` 的 promoted 策略，MaxDrawdown 要等亏损在 ≥5 笔
    里兑现才锁、拦不住第一笔；本闸门在撮合前直接挡掉。

    ``factory=None``（风控禁用）或未配 ``max_order_notional`` → pass-through。
    名义价值以订单**计价货币**计（``quantity * ref_price``）——跨币种精确折算留 follow-up。

    Raises:
        ConflictError: 409 ``RISK_REJECTED``，``rule_name='MaxOrderNotional'``。
            与 enforce 抛同一异常类型，故 HTTP→409 / live runner→记 risk_rejected 决策行
            两条路径都已天然处理，无需新分支。
    """
    if factory is None:
        return
    cap = factory.max_order_notional
    if cap is None:
        return
    notional = abs(quantity) * ref_price
    if notional <= cap:
        return
    reason = (
        f"order notional {notional:.2f} exceeds max_order_notional {cap:.2f} "
        f"({symbol}@{venue}, qty={quantity} × ref={ref_price})"
    )
    raise ConflictError(
        f"order rejected by risk rule: [MaxOrderNotional] {reason}",
        code="RISK_REJECTED",
        details={
            "rule_name": "MaxOrderNotional",
            "reason": reason,
            "notional": notional,
            "max_order_notional": cap,
            "venue": venue,
            "symbol": symbol,
        },
    )


async def enforce(
    factory: RiskGuardFactory | None,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    side: str,
) -> None:
    """HTTP route 一行调用入口：按 account_id 拿对应 RiskGuard 后跑 check。

    ``factory=None`` → fail-open（``INALPHA_RISK_ENGINE_ENABLED=false`` 或 TOML 加载失败时
    走这条；不阻塞下单，但 lifespan 已 log warning / error）。

    **独立连接 + 显式 commit**：本函数内部用 ``get_conn()`` 拿独立 connection 写
    ``risk_locks`` 行，**不复用**调用方 endpoint 的 ``DBConn``。原因：endpoint 命中
    后抛 ConflictError → FastAPI dependency 退出时回滚事务 → 锁也跟着丢；用独立
    connection 保证锁能持久化，下一次同条件请求才能命中 ``from_existing_lock=True``。

    Args:
        factory: ``app.state.risk_guard_factory`` 取出来的实例（可能为 None）
        account_id: caller 派生的 account UUID（来自 JWT sub），用于隔离 trade history
        venue: 如 ``'binance'`` / ``'nasdaq'``
        symbol: 如 ``'BTC/USDT'`` / ``'AAPL'``
        side: ``'BUY'`` / ``'SELL'``（API 层用 BUY/SELL 命名）

    Raises:
        ConflictError: 409 ``RISK_REJECTED``，details 含 rule_name / reason /
            locked_until / lock_scope / from_existing_lock
    """
    if factory is None:
        return

    guard = await factory.get_for_check(account_id)
    instrument_id = InstrumentId(symbol=symbol, venue=venue)
    rule_side = _http_side_to_rule_side(side)
    async with get_conn() as lock_conn:
        rejection = await guard.check(
            lock_conn,
            instrument_id=instrument_id,
            side=rule_side,
            now=datetime.now(UTC),
        )
        # 显式 commit：即使 caller 后续抛异常，锁也已持久化
        # （psycopg 默认 connection 退出时仅在无异常时 commit；本函数自己控制）
        await lock_conn.commit()

    if rejection is None:
        return

    raise ConflictError(
        f"order rejected by risk rule: {rejection.reason}",
        code="RISK_REJECTED",
        details={
            "rule_name": rejection.rule_name,
            "reason": rejection.reason,
            "locked_until": rejection.locked_until.isoformat(),
            "lock_scope": rejection.lock_scope,
            "from_existing_lock": rejection.from_existing_lock,
        },
    )

"""ADR-0006 端到端集成测试：5 件套同时 enabled，覆盖多 rule 协作 + lock 复用 + 多市场。

完整链路：

    config (TOML / dict)
       │ build_rules(trade_repo, market_calendar)
       ▼
    list[RiskRule]
       │ RiskEngine(msgbus, rules, clock, lock_store)
       ▼
    msgbus.send(SubmitOrderCommand)
       │ RiskEngine._handle: 已锁? rules? → publish OrderRejected
       ▼
    events.order.<strategy_id>

不依赖真 DB（用 InMemoryLockStore + mock TradeRepository）。
不依赖 BacktestEngine 改动（直接拼装组件）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.execution.exchange import EXECUTION_ENGINE_ENDPOINT
from inalpha_paper.execution.risk_engine import RiskEngine
from inalpha_paper.execution.risk_rules import (
    ClosedTradeRecord,
    InMemoryLockStore,
    RiskRulesConfig,
    build_rules,
)
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.events import OrderRejected
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT

# ─── shared mocks ───


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _clock_at(dt: datetime) -> TestClock:
    return TestClock(initial_ns=int(dt.timestamp() * 1_000_000_000))


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _aapl() -> InstrumentId:
    return InstrumentId(symbol="AAPL", venue="nasdaq")


def _trade(
    instrument_id: InstrumentId,
    close_ts: datetime,
    profit_pct: float,
    exit_reason: str,
    side: Side = "long",
) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=instrument_id,
        side=side,
        open_ts=close_ts - timedelta(minutes=5),
        close_ts=close_ts,
        close_profit_pct=profit_pct,
        close_profit_abs=profit_pct * 100.0,
        exit_reason=exit_reason,
    )


class _Repo:
    """精简 TradeRepository。支持所有 RiskRule 调用的字段。"""

    def __init__(self, trades: list[ClosedTradeRecord]) -> None:
        self._trades = trades

    def get_closed_trades(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        close_after: datetime,
        close_before: datetime | None = None,
        side: Side | None = None,
        exit_reasons: list[str] | None = None,
        max_profit_pct: float | None = None,
    ) -> list[ClosedTradeRecord]:
        out = [t for t in self._trades if t.close_ts >= close_after]
        if close_before is not None:
            out = [t for t in out if t.close_ts < close_before]
        if instrument_id is not None:
            out = [t for t in out if t.instrument_id == instrument_id]
        if side is not None and side != "*":
            out = [t for t in out if t.side == side]
        if exit_reasons is not None:
            out = [t for t in out if t.exit_reason in exit_reasons]
        if max_profit_pct is not None:
            out = [t for t in out if t.close_profit_pct < max_profit_pct]
        return out


class _Calendar:
    """简单 calendar mock：构造时给一组开放 market；其他 market 闭市。"""

    def __init__(self, open_markets: set[str], next_open: datetime | None = None) -> None:
        self._open = open_markets
        self._next_open = next_open

    def is_trading_hours(
        self, market: str, now: datetime, *, include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return market in self._open

    def next_session_open(self, market: str, now: datetime) -> datetime:
        return self._next_open or now + timedelta(hours=1)


def _submit_cmd(
    client_id: str,
    instrument_id: InstrumentId,
    *,
    side: OrderSide = OrderSide.BUY,
    ts_init: int = 1000,
) -> SubmitOrderCommand:
    return SubmitOrderCommand(
        order=Order(
            client_order_id=ClientOrderId(client_id),
            instrument_id=instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=1.0,
        ),
        strategy_id=StrategyId("e2e-strategy"),
        ts_init=ts_init,
    )


# ─── fixtures ───


@pytest.fixture
def captures() -> tuple[list[object], list[OrderRejected]]:
    """forwarded + rejections 双通道捕获。"""
    return [], []


def _wire_bus(
    bus: MessageBus, forwarded: list[object], rejections: list[OrderRejected]
) -> None:
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))
    bus.subscribe("events.order.e2e-strategy", lambda e: rejections.append(e))


# ─── E2E 1：5 件套全集 + 无历史 trade + 交易时段 → 放行 ───


def test_full_suite_passes_when_no_history(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    now = _utc(2026, 5, 26, 12, 0)
    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "MaxDrawdownRule"},
                {"name": "StoplossGuardRule"},
                {"name": "MarketHoursRule"},
                {"name": "CooldownRule"},
                {"name": "LowProfitRule", "trade_limit": 4, "required_profit": -0.05},
            ]
        }
    )
    rules = build_rules(
        cfg, trade_repo=_Repo([]), market_calendar=_Calendar({"binance"})
    )
    _ = RiskEngine(bus, rules=rules, clock=_clock_at(now))

    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _btc()))

    assert len(forwarded) == 1  # 5 件套全过，放行
    assert rejections == []


# ─── E2E 2：MarketHoursRule 拦 nasdaq（闭市）+ 放行 binance ───


def test_market_hours_filters_per_venue(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    now = _utc(2026, 5, 26, 12, 0)
    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    next_open_nasdaq = _utc(2026, 5, 26, 13, 30)
    rules = build_rules(
        cfg,
        trade_repo=_Repo([]),
        market_calendar=_Calendar({"binance"}, next_open=next_open_nasdaq),
    )
    engine = RiskEngine(bus, rules=rules, clock=_clock_at(now))

    # nasdaq 闭市 → 拒
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _aapl()))
    assert len(rejections) == 1
    assert "[MarketHoursRule]" in rejections[0].reason
    assert "nasdaq" in rejections[0].reason

    # binance 开市 → 放行
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-2", _btc()))
    assert len(forwarded) == 1

    # nasdaq 已有 market 级 lock → 第二次 nasdaq submit 走 existing lock
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-3", _aapl()))
    assert len(rejections) == 2
    assert "已锁" in rejections[1].reason  # 复用 lock 路径
    actives = engine.lock_store.list_active(now)
    # 只 1 个 market 锁（c-1 触发的），c-3 复用，不新增
    assert len(actives) == 1
    assert actives[0].scope == "market"


# ─── E2E 3：StoplossGuardRule 全局 + LowProfitRule 单 symbol 协作 ───


def test_global_stoploss_blocks_all_symbols(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    now = _utc(2026, 5, 26, 12, 0)
    # 全 BTC 连续 3 次止损（不同 instrument 也可以，但用 BTC 让 LowProfit 也可能触发）
    trades = [
        _trade(_btc(), _utc(2026, 5, 26, 11, 30), -0.02, "stop_loss"),
        _trade(_btc(), _utc(2026, 5, 26, 11, 40), -0.03, "stop_loss"),
        _trade(_btc(), _utc(2026, 5, 26, 11, 55), -0.04, "trailing_stop_loss"),
    ]

    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "StoplossGuardRule", "trade_limit": 3, "lookback_min": 60},
                # LowProfit 也能触发，但 StoplossGuard 是 global，先命中
                {"name": "LowProfitRule", "trade_limit": 3, "required_profit": -0.05},
                {"name": "MarketHoursRule"},
            ]
        }
    )
    rules = build_rules(
        cfg, trade_repo=_Repo(trades), market_calendar=_Calendar({"binance", "nasdaq"})
    )
    _ = RiskEngine(bus, rules=rules, clock=_clock_at(now))

    # BTC 拒 → StoplossGuard global 命中（不是 LowProfit symbol）
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _btc()))
    assert len(rejections) == 1
    assert "[StoplossGuardRule]" in rejections[0].reason
    assert "3 次止损" in rejections[0].reason
    # 因为 global 锁 → AAPL 也被拦（复用 global lock）
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-2", _aapl()))
    assert len(rejections) == 2
    assert "已锁" in rejections[1].reason
    assert forwarded == []  # 都没转发


# ─── E2E 4：unlock_at 路径走通 ───


def test_unlock_at_path_in_e2e(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    now = _utc(2026, 5, 26, 12, 0)
    recent_trade = _trade(_btc(), _utc(2026, 5, 26, 11, 55), 0.01, "manual")
    cfg = RiskRulesConfig.model_validate(
        {
            "rules": [
                {"name": "CooldownRule", "unlock_at": "15:00", "lookback_min": 60},
                {"name": "MarketHoursRule"},
            ]
        }
    )
    rules = build_rules(
        cfg, trade_repo=_Repo([recent_trade]), market_calendar=_Calendar({"binance"})
    )
    engine = RiskEngine(bus, rules=rules, clock=_clock_at(now))

    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _btc()))
    assert len(rejections) == 1
    actives = engine.lock_store.list_active(now)
    assert len(actives) == 1
    # 解锁时间应是当天 15:00
    assert actives[0].locked_until == _utc(2026, 5, 26, 15, 0)


# ─── E2E 5：用真实 TOML 文件 + 多 rule 协作 ───


def test_real_toml_config_e2e(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    """用 services/paper/configs/risk_rules.toml 真实默认配置跑全链路。"""
    from pathlib import Path

    from inalpha_paper.execution.risk_rules import load_risk_rules_config

    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    repo_root = Path(__file__).resolve().parent.parent
    cfg = load_risk_rules_config(repo_root / "configs" / "risk_rules.toml")
    assert cfg.enabled is True
    assert len(cfg.rules) == 5

    now = _utc(2026, 5, 26, 12, 0)
    # 5 分钟前有平仓 → CooldownRule (lookback=5) 命中
    recent_trade = _trade(_btc(), _utc(2026, 5, 26, 11, 56), 0.01, "manual")
    rules = build_rules(
        cfg,
        trade_repo=_Repo([recent_trade]),
        market_calendar=_Calendar({"binance"}),
    )
    _ = RiskEngine(bus, rules=rules, clock=_clock_at(now))

    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _btc()))
    assert len(rejections) == 1
    assert "[CooldownRule]" in rejections[0].reason
    assert forwarded == []


# ─── E2E 6：LockStore 持久化效果（多 cmd 间） ───


def test_lock_store_persists_across_commands(
    captures: tuple[list[object], list[OrderRejected]]
) -> None:
    forwarded, rejections = captures
    bus = MessageBus()
    _wire_bus(bus, forwarded, rejections)

    now = _utc(2026, 5, 26, 12, 0)
    # 提前注入 store 一条 active lock（模拟来自历史或 reconcile worker）
    store = InMemoryLockStore()
    from inalpha_paper.execution.risk_rules import RiskVerdict
    pre_seeded = RiskVerdict(
        until=_utc(2026, 5, 26, 13, 0),
        reason="预热的锁（来自之前 session）",
        rule_name="LegacyRule",
        lock_scope="symbol",
    )
    store.add(pre_seeded, instrument_id=_btc(), now=_utc(2026, 5, 26, 11, 0))

    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    rules = build_rules(
        cfg, trade_repo=_Repo([]), market_calendar=_Calendar({"binance"})
    )
    _ = RiskEngine(bus, rules=rules, clock=_clock_at(now), lock_store=store)

    # BTC 因 pre-seeded lock 被拒
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-1", _btc()))
    assert len(rejections) == 1
    assert "[LegacyRule]" in rejections[0].reason
    assert "已锁" in rejections[0].reason

    # AAPL 走 rule（MarketHoursRule 闭市 → 拒）
    bus.send(RISK_ENGINE_ENDPOINT, _submit_cmd("c-2", _aapl()))
    assert len(rejections) == 2
    assert "[MarketHoursRule]" in rejections[1].reason
    assert forwarded == []

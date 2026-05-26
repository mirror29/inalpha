"""`RiskRule` 抽象基类 + 配置解析 + `calculate_lock_end`。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.execution.risk_rules import (
    ClosedTradeRecord,
    RiskRule,
    RiskRuleConfigError,
    RiskVerdict,
    TradeRepository,
)
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.identifiers import InstrumentId

# ─── helpers ───


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class _StubRepo:
    """空的 TradeRepository（基类测试用）。"""

    def get_closed_trades(self, **kwargs: object) -> list[ClosedTradeRecord]:
        return []


class _ConcreteRule(RiskRule):
    """测试用最小子类。"""

    has_symbol_check = True

    def short_desc(self) -> str:
        return "test"


# ─── RiskVerdict 字段 ───


def test_verdict_is_frozen() -> None:
    v = RiskVerdict(
        until=_utc(2026, 5, 26, 12, 0),
        reason="r",
        rule_name="X",
    )
    with pytest.raises(AttributeError):
        v.reason = "y"  # type: ignore[misc]


def test_verdict_defaults() -> None:
    v = RiskVerdict(until=_utc(2026, 5, 26, 12, 0), reason="r", rule_name="X")
    assert v.lock_side == "*"
    assert v.lock_scope == "symbol"
    assert v.lock_market is None


# ─── 配置解析 ───


def test_default_stop_duration() -> None:
    rule = _ConcreteRule({}, _StubRepo())
    assert rule._stop_duration_min == 60
    assert rule._unlock_at is None
    assert rule._lookback_min == 60


def test_custom_stop_duration() -> None:
    rule = _ConcreteRule({"stop_duration_min": 30, "lookback_min": 120}, _StubRepo())
    assert rule._stop_duration_min == 30
    assert rule._lookback_min == 120


def test_unlock_at_parsing() -> None:
    rule = _ConcreteRule({"unlock_at": "13:30"}, _StubRepo())
    assert rule._unlock_at == (13, 30)


def test_unlock_at_invalid_format() -> None:
    with pytest.raises(RiskRuleConfigError, match="HH:MM"):
        _ConcreteRule({"unlock_at": "25:00"}, _StubRepo())
    with pytest.raises(RiskRuleConfigError, match="HH:MM"):
        _ConcreteRule({"unlock_at": "not-a-time"}, _StubRepo())


def test_duration_and_unlock_at_conflict() -> None:
    with pytest.raises(RiskRuleConfigError, match="不能同时配置"):
        _ConcreteRule({"stop_duration_min": 30, "unlock_at": "13:00"}, _StubRepo())


def test_non_positive_duration_rejected() -> None:
    with pytest.raises(RiskRuleConfigError, match="positive"):
        _ConcreteRule({"stop_duration_min": 0}, _StubRepo())
    with pytest.raises(RiskRuleConfigError, match="positive"):
        _ConcreteRule({"stop_duration_min": -5}, _StubRepo())


def test_non_positive_lookback_rejected() -> None:
    with pytest.raises(RiskRuleConfigError, match="lookback_min"):
        _ConcreteRule({"lookback_min": 0}, _StubRepo())


# ─── calculate_lock_end ───


def _trade_at(close_ts: datetime) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=_btc(),
        side="long",
        open_ts=close_ts - timedelta(minutes=10),
        close_ts=close_ts,
        close_profit_pct=0.01,
        close_profit_abs=10.0,
        exit_reason="manual",
    )


def test_lock_end_with_duration() -> None:
    rule = _ConcreteRule({"stop_duration_min": 30}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    trade_close = _utc(2026, 5, 26, 11, 55)  # 5 分钟前平仓
    until = rule.calculate_lock_end([_trade_at(trade_close)], now)
    assert until == trade_close + timedelta(minutes=30)


def test_lock_end_picks_latest_trade() -> None:
    rule = _ConcreteRule({"stop_duration_min": 10}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    trades = [
        _trade_at(_utc(2026, 5, 26, 11, 30)),
        _trade_at(_utc(2026, 5, 26, 11, 55)),  # latest
        _trade_at(_utc(2026, 5, 26, 11, 45)),
    ]
    until = rule.calculate_lock_end(trades, now)
    assert until == _utc(2026, 5, 26, 11, 55) + timedelta(minutes=10)


def test_lock_end_empty_trades_uses_now() -> None:
    rule = _ConcreteRule({"stop_duration_min": 30}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    until = rule.calculate_lock_end([], now)
    assert until == now + timedelta(minutes=30)


def test_lock_end_with_unlock_at_today() -> None:
    rule = _ConcreteRule({"unlock_at": "15:00"}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    trade_close = _utc(2026, 5, 26, 11, 30)
    until = rule.calculate_lock_end([_trade_at(trade_close)], now)
    assert until == _utc(2026, 5, 26, 15, 0)


def test_lock_end_with_unlock_at_rolls_to_next_day() -> None:
    rule = _ConcreteRule({"unlock_at": "09:00"}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    trade_close = _utc(2026, 5, 26, 14, 0)  # 已过当日 9:00
    until = rule.calculate_lock_end([_trade_at(trade_close)], now)
    assert until == _utc(2026, 5, 27, 9, 0)


# ─── 抽象基类不能直接实例化 ───


def test_riskrule_abstract() -> None:
    with pytest.raises(TypeError, match="abstract"):
        RiskRule({}, _StubRepo())  # type: ignore[abstract]


# ─── 默认 check_* 返回 None ───


def test_default_checks_return_none() -> None:
    rule = _ConcreteRule({"stop_duration_min": 30}, _StubRepo())
    now = _utc(2026, 5, 26, 12, 0)
    side: Side = "long"
    assert rule.check_global(now, side, 10_000.0) is None
    assert rule.check_market("binance", now, side, 10_000.0) is None


# ─── TradeRepository Protocol 运行时检查 ───


def test_traderepository_is_runtime_checkable() -> None:
    assert isinstance(_StubRepo(), TradeRepository)

"""`StoplossGuardRule` —— 连续止损则锁。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.execution.risk_rules import ClosedTradeRecord, StoplossGuardRule
from inalpha_paper.execution.risk_rules.base import RiskRuleConfigError, Side
from inalpha_paper.kernel.identifiers import InstrumentId


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _trade(
    close_ts: datetime,
    profit_pct: float,
    exit_reason: str,
    side: Side = "long",
    instrument_id: InstrumentId | None = None,
) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=instrument_id or _btc(),
        side=side,
        open_ts=close_ts - timedelta(minutes=5),
        close_ts=close_ts,
        close_profit_pct=profit_pct,
        close_profit_abs=profit_pct * 100.0,
        exit_reason=exit_reason,
    )


class _Repo:
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


# ─── 不触发 ───


def test_below_trade_limit_no_lock() -> None:
    """2 次止损 < 3 阈值 → 不锁。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.02, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 30), -0.03, "stop_loss"),
        ]
    )
    rule = StoplossGuardRule(
        {"trade_limit": 3, "lookback_min": 60}, repo
    )
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_non_stoploss_exit_ignored() -> None:
    """take_profit / manual 等不算止损。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.05, "manual"),
            _trade(_utc(2026, 5, 26, 11, 15), -0.05, "take_profit"),
        ]
    )
    rule = StoplossGuardRule({"trade_limit": 1, "lookback_min": 60}, repo)
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


# ─── 触发 ───


def test_global_count_triggers() -> None:
    """全局 3 次止损 → 锁全局。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.02, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 15), -0.03, "trailing_stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 30), -0.05, "liquidation"),
        ]
    )
    rule = StoplossGuardRule(
        {"trade_limit": 3, "lookback_min": 60, "stop_duration_min": 120}, repo
    )
    verdict = rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None
    assert verdict.lock_scope == "global"
    assert "3 次止损" in verdict.reason


def test_symbol_scope_triggers() -> None:
    """单 symbol 3 次止损 → 锁该 symbol。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.02, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 15), -0.02, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 30), -0.02, "stop_loss"),
        ]
    )
    rule = StoplossGuardRule({"trade_limit": 3, "lookback_min": 60}, repo)
    verdict = rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None
    assert verdict.lock_scope == "symbol"


def test_only_per_symbol_disables_global() -> None:
    """only_per_symbol=True 时 global 始终返回 None。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.02, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 15), -0.03, "stop_loss"),
            _trade(_utc(2026, 5, 26, 11, 30), -0.05, "stop_loss"),
        ]
    )
    rule = StoplossGuardRule(
        {"trade_limit": 3, "lookback_min": 60, "only_per_symbol": True}, repo
    )
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None
    # symbol check 仍然能触发
    assert rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0) is not None


def test_only_per_side_single_direction() -> None:
    """only_per_side=True 时只看本方向。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), -0.02, "stop_loss", side="long"),
            _trade(_utc(2026, 5, 26, 11, 15), -0.03, "stop_loss", side="short"),
            _trade(_utc(2026, 5, 26, 11, 30), -0.05, "stop_loss", side="short"),
        ]
    )
    rule = StoplossGuardRule(
        {"trade_limit": 2, "lookback_min": 60, "only_per_side": True}, repo
    )
    # long 方向只有 1 笔，不够
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None
    # short 方向有 2 笔，触发
    verdict = rule.check_global(_utc(2026, 5, 26, 12, 0), "short", 10_000.0)
    assert verdict is not None
    assert verdict.lock_side == "short"


# ─── 配置 ───


def test_invalid_trade_limit() -> None:
    with pytest.raises(RiskRuleConfigError, match="trade_limit"):
        StoplossGuardRule({"trade_limit": 0}, _Repo([]))


def test_has_both_global_and_symbol_check() -> None:
    assert StoplossGuardRule.has_global_check is True
    assert StoplossGuardRule.has_symbol_check is True
    assert StoplossGuardRule.has_market_check is False


def test_short_desc_includes_scope() -> None:
    rule = StoplossGuardRule({"trade_limit": 5, "lookback_min": 60}, _Repo([]))
    assert "5 次止损" in rule.short_desc()
    assert "全局" in rule.short_desc()

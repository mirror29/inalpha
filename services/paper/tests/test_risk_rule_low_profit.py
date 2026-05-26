"""`LowProfitRule` —— 单 symbol 累计盈亏低于阈值则锁。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.execution.risk_rules import ClosedTradeRecord, LowProfitRule
from inalpha_paper.execution.risk_rules.base import RiskRuleConfigError, Side
from inalpha_paper.kernel.identifiers import InstrumentId


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _trade(
    close_ts: datetime, profit_pct: float, side: Side = "long"
) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=_btc(),
        side=side,
        open_ts=close_ts - timedelta(minutes=5),
        close_ts=close_ts,
        close_profit_pct=profit_pct,
        close_profit_abs=profit_pct * 100.0,
        exit_reason="manual",
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
        return out


# ─── 触发 / 不触发 ───


def test_not_enough_trades_no_lock() -> None:
    """trade_limit=3 但只有 2 笔 → 不触发。"""
    repo = _Repo([_trade(_utc(2026, 5, 26, 11, 30), -0.10), _trade(_utc(2026, 5, 26, 11, 40), -0.05)])
    rule = LowProfitRule(
        {"trade_limit": 3, "required_profit": 0.0, "lookback_min": 60}, repo
    )
    assert rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_cumulative_above_threshold_no_lock() -> None:
    """累计盈利 > 阈值 → 不触发。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 30), 0.05),
            _trade(_utc(2026, 5, 26, 11, 40), 0.02),
        ]
    )
    rule = LowProfitRule(
        {"trade_limit": 2, "required_profit": 0.0, "lookback_min": 60}, repo
    )
    assert rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_cumulative_below_threshold_locks() -> None:
    """累计 -7% < 0% → 触发。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 30), -0.03),
            _trade(_utc(2026, 5, 26, 11, 40), -0.04),
        ]
    )
    rule = LowProfitRule(
        {"trade_limit": 2, "required_profit": 0.0, "lookback_min": 60,
         "stop_duration_min": 30},
        repo,
    )
    verdict = rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None
    assert verdict.rule_name == "LowProfitRule"
    assert verdict.lock_scope == "symbol"
    assert "-7.00%" in verdict.reason


def test_only_per_side_filters() -> None:
    """only_per_side=True 时只看本方向的 trade。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 30), -0.10, side="long"),
            _trade(_utc(2026, 5, 26, 11, 40), 0.10, side="short"),  # 反向，应忽略
        ]
    )
    rule = LowProfitRule(
        {"trade_limit": 1, "required_profit": 0.0, "lookback_min": 60,
         "only_per_side": True},
        repo,
    )
    verdict = rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None
    assert verdict.lock_side == "long"  # 单边锁


def test_trade_outside_lookback_not_counted() -> None:
    """超出 lookback 的 trade 不计入。"""
    repo = _Repo(
        [
            # 75 min 前 - 在 60 min lookback 外
            _trade(_utc(2026, 5, 26, 10, 45), -0.10),
            # 5 min 前 - 在 lookback 内但单笔不够
            _trade(_utc(2026, 5, 26, 11, 55), -0.01),
        ]
    )
    rule = LowProfitRule(
        {"trade_limit": 2, "required_profit": 0.0, "lookback_min": 60}, repo
    )
    assert rule.check_symbol(_btc(), _utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


# ─── 配置 ───


def test_invalid_trade_limit() -> None:
    with pytest.raises(RiskRuleConfigError, match="trade_limit"):
        LowProfitRule({"trade_limit": 0}, _Repo([]))


def test_has_only_symbol_check() -> None:
    assert LowProfitRule.has_symbol_check is True
    assert LowProfitRule.has_global_check is False
    assert LowProfitRule.has_market_check is False


def test_short_desc() -> None:
    rule = LowProfitRule({"trade_limit": 4, "required_profit": -0.05}, _Repo([]))
    desc = rule.short_desc()
    assert "-5.00%" in desc
    assert "4 笔" in desc

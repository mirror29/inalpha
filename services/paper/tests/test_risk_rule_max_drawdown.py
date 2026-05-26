"""`MaxDrawdownRule` —— 全局回撤超阈值锁。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.execution.risk_rules import ClosedTradeRecord, MaxDrawdownRule
from inalpha_paper.execution.risk_rules.base import RiskRuleConfigError, Side
from inalpha_paper.kernel.identifiers import InstrumentId


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _trade(close_ts: datetime, profit_abs: float) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=_btc(),
        side="long",
        open_ts=close_ts - timedelta(minutes=5),
        close_ts=close_ts,
        close_profit_pct=profit_abs / 10_000.0,
        close_profit_abs=profit_abs,
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
        return out


# ─── 触发 / 不触发 ───


def test_no_drawdown_no_lock() -> None:
    """全是盈利 trade → equity 单调上升，无回撤。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), +500.0),
            _trade(_utc(2026, 5, 26, 11, 30), +300.0),
        ]
    )
    rule = MaxDrawdownRule(
        {"max_drawdown": 0.10, "lookback_min": 1440, "trade_limit": 1}, repo
    )
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_below_threshold_no_lock() -> None:
    """8% 回撤 < 10% 阈值 → 不锁。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), +1000.0),  # equity 11000 (peak)
            _trade(_utc(2026, 5, 26, 11, 30), -800.0),  # equity 10200, dd ~ 7.27%
        ]
    )
    rule = MaxDrawdownRule(
        {"max_drawdown": 0.10, "lookback_min": 1440, "trade_limit": 1}, repo
    )
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_above_threshold_locks() -> None:
    """15% 回撤 > 10% 阈值 → 锁。"""
    repo = _Repo(
        [
            _trade(_utc(2026, 5, 26, 11, 0), +1000.0),  # equity 11000 (peak)
            _trade(_utc(2026, 5, 26, 11, 30), -2000.0),  # equity 9000, dd ~ 18.18%
        ]
    )
    rule = MaxDrawdownRule(
        {"max_drawdown": 0.10, "lookback_min": 1440, "trade_limit": 1,
         "stop_duration_min": 120},
        repo,
    )
    verdict = rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None
    assert verdict.lock_scope == "global"
    assert verdict.lock_side == "*"
    assert "10.00%" in verdict.reason


def test_not_enough_trades_no_lock() -> None:
    """trade_limit=5 但只有 1 笔 → 不触发评估。"""
    repo = _Repo([_trade(_utc(2026, 5, 26, 11, 30), -2000.0)])
    rule = MaxDrawdownRule(
        {"max_drawdown": 0.10, "lookback_min": 1440, "trade_limit": 5}, repo
    )
    assert rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0) is None


def test_window_uses_pre_window_profit_as_base() -> None:
    """窗口之前的 +1000 应该计入 base balance，让窗口内回撤计算正确。"""
    repo = _Repo(
        [
            # 窗口外（25h 前）：累计 +1000，提升 base 到 11000
            _trade(_utc(2026, 5, 25, 10, 0), +1000.0),
            # 窗口内（4h 内）：先 +500（peak=11500）然后 -2000（trough=9500）
            _trade(_utc(2026, 5, 26, 9, 0), +500.0),
            _trade(_utc(2026, 5, 26, 11, 0), -2000.0),
        ]
    )
    rule = MaxDrawdownRule(
        {"max_drawdown": 0.10, "lookback_min": 240, "trade_limit": 1}, repo
    )
    # base = 10000 + 1000 (pre-window) = 11000
    # window: 11000 +500 = 11500 (peak), -2000 = 9500
    # dd = (11500 - 9500) / 11500 = 17.39% > 10% → 锁
    verdict = rule.check_global(_utc(2026, 5, 26, 12, 0), "long", 10_000.0)
    assert verdict is not None


# ─── 配置 ───


def test_invalid_max_drawdown_zero() -> None:
    with pytest.raises(RiskRuleConfigError, match="max_drawdown"):
        MaxDrawdownRule({"max_drawdown": 0.0}, _Repo([]))


def test_invalid_max_drawdown_over_one() -> None:
    with pytest.raises(RiskRuleConfigError, match="max_drawdown"):
        MaxDrawdownRule({"max_drawdown": 1.5}, _Repo([]))


def test_has_only_global_check() -> None:
    assert MaxDrawdownRule.has_global_check is True
    assert MaxDrawdownRule.has_symbol_check is False
    assert MaxDrawdownRule.has_market_check is False


def test_short_desc() -> None:
    rule = MaxDrawdownRule({"max_drawdown": 0.15}, _Repo([]))
    assert "15.00%" in rule.short_desc()

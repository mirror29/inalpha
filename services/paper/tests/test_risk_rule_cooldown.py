"""`CooldownRule` —— 单 symbol 冷却期。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inalpha_paper.execution.risk_rules import ClosedTradeRecord, CooldownRule
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.identifiers import InstrumentId

# ─── helpers ───


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _eth() -> InstrumentId:
    return InstrumentId(symbol="ETH/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _trade(
    instrument_id: InstrumentId,
    close_ts: datetime,
    side: Side = "long",
) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        instrument_id=instrument_id,
        side=side,
        open_ts=close_ts - timedelta(minutes=5),
        close_ts=close_ts,
        close_profit_pct=0.01,
        close_profit_abs=10.0,
        exit_reason="manual",
    )


class _Repo:
    """简单 TradeRepository mock。"""

    def __init__(self, trades: list[ClosedTradeRecord]) -> None:
        self._trades = trades

    def get_closed_trades(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        close_after: datetime,
        side: Side | None = None,
        exit_reasons: list[str] | None = None,
        max_profit_pct: float | None = None,
    ) -> list[ClosedTradeRecord]:
        out = self._trades
        if instrument_id is not None:
            out = [t for t in out if t.instrument_id == instrument_id]
        out = [t for t in out if t.close_ts >= close_after]
        if side is not None and side != "*":
            out = [t for t in out if t.side == side]
        if exit_reasons is not None:
            out = [t for t in out if t.exit_reason in exit_reasons]
        if max_profit_pct is not None:
            out = [t for t in out if t.close_profit_pct < max_profit_pct]
        return out


# ─── 触发 / 不触发 ───


def test_no_recent_trades_no_lock() -> None:
    repo = _Repo([])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)
    now = _utc(2026, 5, 26, 12, 0)
    assert rule.check_symbol(_btc(), now, "long", 10_000.0) is None


def test_recent_trade_triggers_lock() -> None:
    now = _utc(2026, 5, 26, 12, 0)
    repo = _Repo([_trade(_btc(), _utc(2026, 5, 26, 11, 55))])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)

    verdict = rule.check_symbol(_btc(), now, "long", 10_000.0)
    assert verdict is not None
    assert verdict.rule_name == "CooldownRule"
    assert verdict.lock_scope == "symbol"
    assert verdict.lock_side == "*"
    assert verdict.until == _utc(2026, 5, 26, 11, 55) + timedelta(minutes=30)
    assert "1 笔" in verdict.reason


def test_trade_outside_lookback_no_lock() -> None:
    now = _utc(2026, 5, 26, 12, 0)
    # 75 分钟前平仓，超出 60 分钟 lookback
    old = _trade(_btc(), _utc(2026, 5, 26, 10, 45))
    repo = _Repo([old])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)
    assert rule.check_symbol(_btc(), now, "long", 10_000.0) is None


def test_only_locks_target_symbol() -> None:
    """ETH 有 trade 不应该锁 BTC。"""
    now = _utc(2026, 5, 26, 12, 0)
    repo = _Repo([_trade(_eth(), _utc(2026, 5, 26, 11, 55))])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)

    assert rule.check_symbol(_btc(), now, "long", 10_000.0) is None
    assert rule.check_symbol(_eth(), now, "long", 10_000.0) is not None


def test_multiple_trades_picks_latest() -> None:
    now = _utc(2026, 5, 26, 12, 0)
    repo = _Repo(
        [
            _trade(_btc(), _utc(2026, 5, 26, 11, 30)),
            _trade(_btc(), _utc(2026, 5, 26, 11, 50)),  # latest
            _trade(_btc(), _utc(2026, 5, 26, 11, 40)),
        ]
    )
    rule = CooldownRule({"stop_duration_min": 10, "lookback_min": 60}, repo)

    verdict = rule.check_symbol(_btc(), now, "long", 10_000.0)
    assert verdict is not None
    assert verdict.until == _utc(2026, 5, 26, 11, 50) + timedelta(minutes=10)
    assert "3 笔" in verdict.reason


def test_unlock_at_path() -> None:
    """使用 unlock_at 而非 stop_duration_min。"""
    now = _utc(2026, 5, 26, 12, 0)
    repo = _Repo([_trade(_btc(), _utc(2026, 5, 26, 11, 55))])
    rule = CooldownRule({"unlock_at": "15:00", "lookback_min": 60}, repo)

    verdict = rule.check_symbol(_btc(), now, "long", 10_000.0)
    assert verdict is not None
    assert verdict.until == _utc(2026, 5, 26, 15, 0)


# ─── 能力声明 ───


def test_has_only_symbol_check() -> None:
    assert CooldownRule.has_symbol_check is True
    assert CooldownRule.has_global_check is False
    assert CooldownRule.has_market_check is False


def test_short_desc_with_duration() -> None:
    repo = _Repo([])
    rule = CooldownRule({"stop_duration_min": 45}, repo)
    assert "45 分钟" in rule.short_desc()


def test_short_desc_with_unlock_at() -> None:
    repo = _Repo([])
    rule = CooldownRule({"unlock_at": "09:30"}, repo)
    assert "09:30" in rule.short_desc()

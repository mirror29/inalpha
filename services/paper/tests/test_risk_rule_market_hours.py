"""`MarketHoursRule` 单测 —— 用 stub calendar 验证 rule 编排逻辑（不依赖
exchange_calendars，真日历接入见 `test_market_calendar.py`）。

接口变更（D-9.1a 多市场）：``check_market`` 收 ``InstrumentId``（取 venue+symbol），
``lock_market`` 写交易所日历 code（无法解析 fallback venue）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inalpha_paper.execution.risk_rules import ClosedTradeRecord, MarketHoursRule
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.identifiers import InstrumentId


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _instr(venue: str, symbol: str) -> InstrumentId:
    return InstrumentId(symbol=symbol, venue=venue)


class _EmptyRepo:
    def get_closed_trades(self, **kwargs: object) -> list[ClosedTradeRecord]:
        return []


class _StubCalendar:
    """简单 calendar mock：按 venue 判断开市 + 提供 next_open。"""

    def __init__(
        self,
        open_venues: set[str],
        next_open: dict[str, datetime] | None = None,
    ) -> None:
        self._open = open_venues
        self._next_open = next_open or {}

    def is_trading_hours(
        self,
        venue: str,
        symbol: str,
        now: datetime,
        *,
        include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return venue in self._open

    def next_session_open(self, venue: str, symbol: str, now: datetime) -> datetime:
        return self._next_open.get(venue, now + timedelta(hours=1))


# ─── 触发 / 不触发 ───


def test_trading_hours_no_lock() -> None:
    """市场在交易时段 → 不锁。"""
    rule = MarketHoursRule({}, _EmptyRepo(), _StubCalendar({"yfinance"}))
    now = _utc(2026, 5, 26, 14, 0)
    assert rule.check_market(_instr("yfinance", "AAPL"), now, "long", 10_000.0) is None


def test_non_trading_hours_locks() -> None:
    """市场闭市 → 锁，until = 下次开盘，lock_market = 解析出的交易所 code。"""
    next_open = _utc(2026, 5, 26, 14, 30)
    rule = MarketHoursRule(
        {},
        _EmptyRepo(),
        _StubCalendar(open_venues=set(), next_open={"yfinance": next_open}),
    )
    now = _utc(2026, 5, 26, 9, 0)  # 美股盘前
    verdict = rule.check_market(_instr("yfinance", "AAPL"), now, "long", 10_000.0)
    assert verdict is not None
    assert verdict.until == next_open
    assert verdict.lock_scope == "market"
    assert verdict.lock_market == "XNYS"  # yfinance + AAPL → XNYS
    assert verdict.lock_side == "*"
    assert "XNYS" in verdict.reason


def test_lock_market_fallback_to_venue() -> None:
    """无法解析交易所（未知 venue）→ lock_market fallback 到 venue。"""
    rule = MarketHoursRule({}, _EmptyRepo(), _StubCalendar(open_venues=set()))
    verdict = rule.check_market(
        _instr("weirdvenue", "XYZ"), _utc(2026, 5, 26, 9, 0), "long", 10_000.0
    )
    assert verdict is not None
    assert verdict.lock_market == "weirdvenue"


def test_crypto_stub_open() -> None:
    """stub 标 crypto 开市 → 不锁（真 crypto 24/7 见 test_market_calendar）。"""
    rule = MarketHoursRule({}, _EmptyRepo(), _StubCalendar({"binance"}))
    for h in (0, 3, 9, 15, 22, 23):
        assert (
            rule.check_market(
                _instr("binance", "BTC/USDT"), _utc(2026, 5, 26, h, 0), "long", 10_000.0
            )
            is None
        )


def test_allow_pre_market_passes_flag() -> None:
    """allow_pre_market=True 时 calendar 收到 include_pre=True。"""
    flags_received: list[bool] = []

    class _SpyCalendar:
        def is_trading_hours(
            self,
            venue: str,
            symbol: str,
            now: datetime,
            *,
            include_pre: bool = False,
            include_after: bool = False,
        ) -> bool:
            flags_received.append(include_pre)
            return True

        def next_session_open(self, venue: str, symbol: str, now: datetime) -> datetime:
            return now

    rule = MarketHoursRule({"allow_pre_market": True}, _EmptyRepo(), _SpyCalendar())
    _ = rule.check_market(
        _instr("yfinance", "AAPL"), _utc(2026, 5, 26, 9, 0), "long", 10_000.0
    )
    assert flags_received == [True]


# ─── 能力声明 ───


def test_has_only_market_check() -> None:
    assert MarketHoursRule.has_market_check is True
    assert MarketHoursRule.has_global_check is False
    assert MarketHoursRule.has_symbol_check is False


def test_short_desc() -> None:
    rule = MarketHoursRule({}, _EmptyRepo(), _StubCalendar(set()))
    assert "非交易时段拦截" in rule.short_desc()

    rule_pre = MarketHoursRule(
        {"allow_pre_market": True, "allow_after_hours": True},
        _EmptyRepo(),
        _StubCalendar(set()),
    )
    desc = rule_pre.short_desc()
    assert "盘前" in desc
    assert "盘后" in desc


def _unused_side_marker() -> Side:
    """单纯让 import Side 不被 ruff F401 报。"""
    return "*"

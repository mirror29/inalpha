"""``_replay_account`` 核心逻辑（ADR-0007 Slice 7）。

不依赖 DB 的单元测试，验证从 orders 表行 → close staging 的纯函数重放。
backfill 脚本本身是一次性运维工具，依赖 DB integration 由 dry-run + 人工确认覆盖。
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

# 脚本位置：services/paper/scripts/backfill_closed_trades.py
_paper_pkg = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_paper_pkg))

from scripts.backfill_closed_trades import _replay_account  # noqa: E402


def _order_row(
    *,
    venue: str = "binance",
    symbol: str = "BTC/USDT",
    side: str = "BUY",
    qty: float = 1.0,
    price: float = 100.0,
    ts: datetime,
    client_order_id: str,
) -> dict[str, Any]:
    return {
        "client_order_id": client_order_id,
        "venue": venue,
        "symbol": symbol,
        "side": side,
        "qty": Decimal(str(qty)),
        "price": Decimal(str(price)),
        "ts_event": ts,
    }


def _ts(hour: int) -> datetime:
    return datetime(2026, 5, 26, hour, 0, tzinfo=UTC)


# ─── 基础场景 ───


def test_open_then_close_yields_one_staging() -> None:
    account = uuid4()
    rows = [
        _order_row(side="BUY", qty=1.0, price=100.0, ts=_ts(10), client_order_id="o-1"),
        _order_row(side="SELL", qty=1.0, price=110.0, ts=_ts(11), client_order_id="o-2"),
    ]
    closes = _replay_account(rows, account)
    assert len(closes) == 1
    s = closes[0]
    assert s.side == "long"
    assert float(s.quantity) == 1.0
    assert s.close_profit_abs == 10.0
    assert s.exit_reason == "signal"  # 历史数据无 tag → 默认
    assert s.open_order_id == "o-1"
    assert s.close_order_id == "o-2"


def test_open_only_no_staging() -> None:
    """只开仓未平 → 0 staging。"""
    rows = [
        _order_row(side="BUY", qty=1.0, price=100.0, ts=_ts(10), client_order_id="o-1"),
    ]
    assert _replay_account(rows, uuid4()) == []


def test_multiple_round_trips() -> None:
    """开-平-开-平 → 2 staging。"""
    rows = [
        _order_row(side="BUY", qty=1.0, price=100.0, ts=_ts(10), client_order_id="o-1"),
        _order_row(side="SELL", qty=1.0, price=105.0, ts=_ts(11), client_order_id="o-2"),
        _order_row(side="BUY", qty=2.0, price=95.0, ts=_ts(12), client_order_id="o-3"),
        _order_row(side="SELL", qty=2.0, price=110.0, ts=_ts(13), client_order_id="o-4"),
    ]
    closes = _replay_account(rows, uuid4())
    assert len(closes) == 2
    assert closes[0].close_profit_abs == 5.0
    assert closes[1].close_profit_abs == 30.0
    assert closes[1].open_order_id == "o-3"  # 第二次开仓的 order


def test_short_position_close() -> None:
    """short 重放：SELL 开 + BUY 平。"""
    rows = [
        _order_row(side="SELL", qty=1.0, price=100.0, ts=_ts(10), client_order_id="o-1"),
        _order_row(side="BUY", qty=1.0, price=90.0, ts=_ts(11), client_order_id="o-2"),
    ]
    closes = _replay_account(rows, uuid4())
    assert len(closes) == 1
    assert closes[0].side == "short"
    assert closes[0].close_profit_abs == 10.0  # (100 - 90) × 1


# ─── 多 symbol 隔离 ───


def test_multiple_symbols_isolated() -> None:
    """BTC 和 ETH 维护独立 Position。"""
    rows = [
        _order_row(symbol="BTC/USDT", side="BUY", ts=_ts(10), client_order_id="btc-1"),
        _order_row(symbol="ETH/USDT", side="BUY", price=2000, ts=_ts(11), client_order_id="eth-1"),
        _order_row(symbol="BTC/USDT", side="SELL", price=110, ts=_ts(12), client_order_id="btc-2"),
        _order_row(symbol="ETH/USDT", side="SELL", price=2100, ts=_ts(13), client_order_id="eth-2"),
    ]
    closes = _replay_account(rows, uuid4())
    assert len(closes) == 2
    symbols = sorted(c.symbol for c in closes)
    assert symbols == ["BTC/USDT", "ETH/USDT"]


# ─── partial close ───


def test_partial_close_then_full_close() -> None:
    """开 3 → 卖 1 → 卖 2 = 2 staging。"""
    rows = [
        _order_row(side="BUY", qty=3.0, price=100.0, ts=_ts(10), client_order_id="o-1"),
        _order_row(side="SELL", qty=1.0, price=110.0, ts=_ts(11), client_order_id="o-2"),
        _order_row(side="SELL", qty=2.0, price=115.0, ts=_ts(12), client_order_id="o-3"),
    ]
    closes = _replay_account(rows, uuid4())
    assert len(closes) == 2
    assert float(closes[0].quantity) == 1.0
    assert float(closes[1].quantity) == 2.0
    # 两次都属于同一开仓
    assert closes[0].open_order_id == "o-1"
    assert closes[1].open_order_id == "o-1"


# ─── 字段透传 ───


def test_account_id_propagates() -> None:
    account = uuid4()
    rows = [
        _order_row(side="BUY", ts=_ts(10), client_order_id="o-1"),
        _order_row(side="SELL", price=110.0, ts=_ts(11), client_order_id="o-2"),
    ]
    closes = _replay_account(rows, account)
    assert closes[0].account_id == account


def test_venue_propagates() -> None:
    rows = [
        _order_row(venue="nasdaq", symbol="AAPL", side="BUY", ts=_ts(10), client_order_id="o-1"),
        _order_row(venue="nasdaq", symbol="AAPL", side="SELL", price=110.0, ts=_ts(11), client_order_id="o-2"),
    ]
    closes = _replay_account(rows, uuid4())
    assert closes[0].venue == "nasdaq"
    assert closes[0].symbol == "AAPL"

"""0036 A 股 bars identity 规范化迁移集成测试。"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "migrations"
    / "versions"
    / "0036_bars_a_share_symbol_canonical.py"
)
_DB_URL = "postgresql://quant:devpass@localhost:5433/inalpha_test"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_0036", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0036_merges_legacy_symbols_without_deleting_unrelated_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """canonical 行优先；legacy 变体合并，非 A 股与畸形代码保留。"""
    ts = datetime(2026, 7, 22, tzinfo=UTC)
    rows = [
        ("baostock", "sh.600519", 10.0),
        ("akshare", "SH.600519", 20.0),
        ("baostock", "600519.SH", 30.0),
        ("akshare", "000001.SZ", 40.0),
        ("yfinance", "0700.HK", 50.0),
        ("baostock", "sh.BAD", 60.0),
    ]

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM bars WHERE ts = %s AND symbol IN "
                "('sh.600519', 'SH.600519', '600519.SH', '000001.SZ', '0700.HK', 'sh.BAD')",
                (ts,),
            )
            cursor.executemany(
                "INSERT INTO bars "
                "(ts, venue, symbol, timeframe, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, '1d', %s, %s, %s, %s, 1)",
                [(ts, venue, symbol, close, close, close, close) for venue, symbol, close in rows],
            )
        conn.commit()

        def _execute(sql: str) -> None:
            with conn.cursor() as cursor:
                cursor.execute(str(sql))

        monkeypatch.setitem(
            sys.modules, "alembic", SimpleNamespace(op=SimpleNamespace(execute=_execute))
        )
        _load_migration().upgrade()
        conn.commit()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT venue, symbol, close FROM bars WHERE ts = %s "
                "AND symbol IN ('sh.600519', 'SH.600519', '600519.SH', 'sz.000001', "
                "'000001.SZ', '0700.HK', 'sh.BAD') ORDER BY venue, symbol",
                (ts,),
            )
            result = cursor.fetchall()

    assert result == [
        ("baostock", "sh.600519", 10.0),
        ("baostock", "sh.BAD", 60.0),
        ("baostock", "sz.000001", 40.0),
        ("yfinance", "0700.HK", 50.0),
    ]

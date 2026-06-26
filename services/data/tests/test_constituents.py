"""指数成分 PIT 快照（#106 / ADR-0053 阶段 C）：connector 归一化（无 DB）+ 存储 time-travel（DB）。"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pandas as pd
import pytest

from inalpha_data.connectors import akshare as ak_conn
from inalpha_data.connectors.akshare import _cn_symbol

# ── connector：符号归一 + 列模糊匹配（无网络/无 DB）────────────────────


def test_cn_symbol_prefix() -> None:
    """6 位 A股代码 → sh./sz./bj. 前缀（6/9沪、0/2/3深、4/8北）。"""
    assert _cn_symbol("600000") == "sh.600000"
    assert _cn_symbol("688981") == "sh.688981"
    assert _cn_symbol("000001") == "sz.000001"
    assert _cn_symbol("300750") == "sz.300750"
    assert _cn_symbol("830799") == "bj.830799"
    assert _cn_symbol("sh.600519") == "sh.600519"  # 已带前缀幂等


def test_fetch_constituents_sync_fuzzy_columns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """akshare 中证列名（成分券代码/名称/权重）模糊抽取 + 归一为 Inalpha 符号。"""
    import akshare

    df = pd.DataFrame(
        {
            "日期": ["2026-06-26", "2026-06-26"],
            "成分券代码": ["600519", "000001"],
            "成分券名称": ["贵州茅台", "平安银行"],
            "权重": [5.2, 1.1],
        }
    )
    monkeypatch.setattr(
        akshare, "index_stock_cons_weight_csindex", lambda symbol: df, raising=False
    )
    out = ak_conn._fetch_constituents_sync(index_code="000300")
    by_code = {o["code"]: o for o in out}
    assert set(by_code) == {"sh.600519", "sz.000001"}
    assert by_code["sh.600519"]["name"] == "贵州茅台"
    assert by_code["sh.600519"]["weight"] == 5.2


def test_fetch_constituents_sync_picks_constituent_not_index_columns(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """真实中证表同含「指数代码/名称」+「成分券代码/名称」→ 必须抽成分列、不是指数自身。

    回归：列优先 + 宽松 "代码" 兜底会先命中排在前的「指数代码」，把每只成分错写成指数
    本身（300 行全 = 000300/沪深300）。_find 改 key 优先后精确「成分券代码」先命中。
    """
    import akshare

    df = pd.DataFrame(
        {
            "日期": ["2026-05-29", "2026-05-29"],
            "指数代码": ["000300", "000300"],  # 指数自身——绝不能被当成分
            "指数名称": ["沪深300", "沪深300"],
            "成分券代码": ["600519", "000001"],
            "成分券名称": ["贵州茅台", "平安银行"],
            "权重": [5.2, 1.1],
        }
    )
    monkeypatch.setattr(
        akshare, "index_stock_cons_weight_csindex", lambda symbol: df, raising=False
    )
    out = ak_conn._fetch_constituents_sync(index_code="000300")
    by_code = {o["code"]: o for o in out}
    assert set(by_code) == {"sh.600519", "sz.000001"}  # 成分，非 sz.000300
    assert by_code["sh.600519"]["name"] == "贵州茅台"  # 成分名，非「沪深300」
    assert by_code["sz.000001"]["weight"] == 1.1


def test_fetch_constituents_sync_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """weight 接口空 → 回退 cons 接口；都空 → []（优雅降级）。"""
    import akshare

    empty = pd.DataFrame()
    monkeypatch.setattr(
        akshare, "index_stock_cons_weight_csindex", lambda symbol: empty, raising=False
    )
    monkeypatch.setattr(
        akshare, "index_stock_cons_csindex", lambda symbol: empty, raising=False
    )
    assert ak_conn._fetch_constituents_sync(index_code="000300") == []


# ── 存储：time-travel + 幂等（真实 DB，5433 inalpha）──────────────────

pytestmark_integration = pytest.mark.integration


@pytest.fixture
def idx() -> str:
    return f"TESTIDX{uuid4().hex[:6]}"


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_time_travel_returns_latest_le_as_of(idx: str) -> None:
    from inalpha_shared.db import get_conn

    from inalpha_data.storage.constituents import get_constituents, upsert_snapshot

    async with get_conn() as conn:
        async with conn.transaction():
            await upsert_snapshot(
                conn, index_code=idx, as_of_date=date(2026, 1, 1),
                constituents=[{"code": "sh.600000", "name": "A", "weight": 1.0}],
            )
            await upsert_snapshot(
                conn, index_code=idx, as_of_date=date(2026, 2, 1),
                constituents=[
                    {"code": "sh.600519", "name": "B"},
                    {"code": "sz.000001", "name": "C"},
                ],
            )
        # as_of 在两份之间 → 返 1/1 那份
        d1, m1 = await get_constituents(conn, index_code=idx, as_of=date(2026, 1, 15))
        assert d1 == date(2026, 1, 1)
        assert [x["code"] for x in m1] == ["sh.600000"]
        # as_of 在第二份之后 → 返 2/1 那份（最近 ≤ as_of）
        d2, m2 = await get_constituents(conn, index_code=idx, as_of=date(2026, 3, 1))
        assert d2 == date(2026, 2, 1)
        assert len(m2) == 2
        # as_of 早于最早快照 → (None, [])，上层标 non-PIT 降级
        d3, m3 = await get_constituents(conn, index_code=idx, as_of=date(2025, 12, 1))
        assert d3 is None
        assert m3 == []


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_upsert_idempotent_same_day_updates(idx: str) -> None:
    from inalpha_shared.db import get_conn

    from inalpha_data.storage.constituents import get_constituents, upsert_snapshot

    async with get_conn() as conn:
        async with conn.transaction():
            await upsert_snapshot(
                conn, index_code=idx, as_of_date=date(2026, 1, 1),
                constituents=[{"code": "sh.600000", "weight": 1.0}],
            )
            await upsert_snapshot(
                conn, index_code=idx, as_of_date=date(2026, 1, 1),
                constituents=[{"code": "sh.600000", "weight": 2.0}],  # 同日重录
            )
        _d, m = await get_constituents(conn, index_code=idx, as_of=date(2026, 1, 1))
    assert len(m) == 1  # 幂等不重复
    assert m[0]["weight"] == 2.0  # 更新权重

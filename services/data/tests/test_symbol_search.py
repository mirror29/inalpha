"""symbol_search connector + GET /symbols/search 端点测试（不打外网）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inalpha_data.connectors import symbol_search as ss
from inalpha_data.connectors.symbol_search import SymbolSearchConnector, _a_share_prefix

pytestmark = pytest.mark.anyio

_FAKE_A_SHARE = [
    {"code": "600519", "name": "贵州茅台"},
    {"code": "000001", "name": "平安银行"},
    {"code": "300750", "name": "宁德时代"},
    {"code": "430047", "name": "某北交所公司"},  # bj：当前不支持，应被跳过
]

_FAKE_YAHOO = [
    {"symbol": "AAPL", "shortname": "Apple Inc.", "exchange": "NMS", "quoteType": "EQUITY"},
    {"symbol": "0700.HK", "shortname": "TENCENT", "exchange": "HKG", "quoteType": "EQUITY"},
]


def test_a_share_prefix_mapping() -> None:
    assert _a_share_prefix("600519") == "sh"
    assert _a_share_prefix("000001") == "sz"
    assert _a_share_prefix("300750") == "sz"
    assert _a_share_prefix("430047") is None  # 北交所暂不支持


async def test_cjk_query_hits_a_share_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "_load_a_share_table_sync", lambda: _FAKE_A_SHARE)
    monkeypatch.setattr(ss, "_yahoo_search_sync", lambda q, n: [])
    conn = SymbolSearchConnector()
    out = await conn.search("茅台")
    assert out == [
        {
            "symbol": "sh.600519",
            "name": "贵州茅台",
            "exchange": "XSHG",
            "venue": "akshare",
            "quote_type": "EQUITY",
        }
    ]


async def test_cjk_auto_queries_both_sources_round_robin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto + 中文：A股表与 Yahoo 并行都查、轮替合并——语言≠市场，
    任一来源不得把另一来源挤出 max_results（跨市场同名歧义两边都呈现）。"""
    yahoo_calls: list[tuple[str, int]] = []

    def fake_yahoo(query: str, max_results: int):
        yahoo_calls.append((query, max_results))
        return _FAKE_YAHOO

    monkeypatch.setattr(ss, "_load_a_share_table_sync", lambda: _FAKE_A_SHARE)
    monkeypatch.setattr(ss, "_yahoo_search_sync", fake_yahoo)
    conn = SymbolSearchConnector()

    out = await conn.search("平安", max_results=4)

    # Yahoo 以完整 max_results 被查（不是 A股填剩的余额）
    assert yahoo_calls == [("平安", 4)]
    # 轮替合并：a1, y1, a2(无), y2 → A股命中 1 条时 Yahoo 两条都进结果
    assert [r["venue"] for r in out] == ["akshare", "yfinance", "yfinance"]
    assert out[0]["symbol"] == "sz.000001"
    assert {r["symbol"] for r in out if r["venue"] == "yfinance"} == {"AAPL", "0700.HK"}


async def test_cjk_with_explicit_yfinance_venue_skips_a_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 venue=yfinance 时即使 query 是中文也不碰 A股表（agent 已判断市场）。"""
    table_loads: list[int] = []

    def fake_load():
        table_loads.append(1)
        return _FAKE_A_SHARE

    monkeypatch.setattr(ss, "_load_a_share_table_sync", fake_load)
    monkeypatch.setattr(ss, "_yahoo_search_sync", lambda q, n: _FAKE_YAHOO)
    conn = SymbolSearchConnector()

    out = await conn.search("特斯拉", venue="yfinance")

    assert table_loads == []  # A股表一次都没加载
    assert [r["venue"] for r in out] == ["yfinance", "yfinance"]


async def test_code_prefix_match_and_bj_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "_load_a_share_table_sync", lambda: _FAKE_A_SHARE)
    conn = SymbolSearchConnector()
    out = await conn.search("430047", venue="akshare")
    assert out == []  # bj 代码被跳过而不是返回错误格式
    out2 = await conn.search("0005", venue="akshare")
    assert [r["symbol"] for r in out2] == []  # 前缀不匹配（000001 不以 0005 开头）
    out3 = await conn.search("00000", venue="akshare")
    assert [r["symbol"] for r in out3] == ["sz.000001"]


async def test_ascii_query_goes_yahoo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_yahoo(query: str, max_results: int):
        calls.append(query)
        return _FAKE_YAHOO

    monkeypatch.setattr(ss, "_yahoo_search_sync", fake_yahoo)
    conn = SymbolSearchConnector()
    out = await conn.search("apple")
    assert calls == ["apple"]
    assert out[0]["symbol"] == "AAPL"
    assert out[0]["venue"] == "yfinance"


async def test_a_share_table_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[int] = []

    def fake_load():
        loads.append(1)
        return _FAKE_A_SHARE

    monkeypatch.setattr(ss, "_load_a_share_table_sync", fake_load)
    conn = SymbolSearchConnector()
    await conn.search("茅台", venue="akshare")
    await conn.search("平安", venue="akshare")
    assert len(loads) == 1  # 第二次走缓存


async def test_table_load_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("akshare down")

    monkeypatch.setattr(ss, "_load_a_share_table_sync", boom)
    conn = SymbolSearchConnector()
    out = await conn.search("茅台", venue="akshare")
    assert out == []  # fail-open


def test_symbols_search_requires_auth(client: TestClient) -> None:
    r = client.get("/symbols/search", params={"query": "apple"})
    assert r.status_code == 401


def test_symbols_search_endpoint_shape(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    original = ss._connector.search

    async def mock_search(query: str, venue: str = "auto", max_results: int = 10):
        return [
            {
                "symbol": "sh.600519",
                "name": "贵州茅台",
                "exchange": "XSHG",
                "venue": "akshare",
                "quote_type": "EQUITY",
            }
        ]

    ss._connector.search = mock_search
    try:
        r = client.get(
            "/symbols/search", headers=auth_headers, params={"query": "茅台"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["results"][0]["symbol"] == "sh.600519"
    finally:
        ss._connector.search = original

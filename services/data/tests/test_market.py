"""Tests for GET /market/* endpoints + CnMarketConnector 归一化（D-12+ 行情归因）。

源站（东财/同花顺）真实网络一律不进 CI：endpoint 测试替换 connector 实例方法，
connector 归一化测试 monkeypatch ``_get_json``。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inalpha_data.connectors import cn_market as cm
from inalpha_data.connectors.cn_market import CnMarketConnector, CnMarketError

pytestmark = pytest.mark.anyio


# ── endpoint 层 ──────────────────────────────────────────────────────


def test_market_news_requires_auth(client: TestClient) -> None:
    r = client.get("/market/news")
    assert r.status_code == 401


def test_market_sectors_requires_auth(client: TestClient) -> None:
    r = client.get("/market/sectors")
    assert r.status_code == 401


def test_market_unsupported_market_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """未实装的 market 返 400 MARKET_NOT_SUPPORTED（不要硬调后静默空）。"""
    r = client.get("/market/news", headers=auth_headers, params={"market": "us"})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "MARKET_NOT_SUPPORTED"
    assert body["details"]["supported"] == ["cn"]


def test_market_news_mock_returns_items(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    original = cm._connector.fetch_market_news

    async def mock_news(limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "title": "测试快讯",
                "summary": "摘要",
                "published_at": "2026-06-12T06:48:09+00:00",
                "related_codes": ["600519"],
            }
        ]

    cm._connector.fetch_market_news = mock_news
    try:
        r = client.get("/market/news", headers=auth_headers, params={"limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["market"] == "cn"
        assert body["fetched_at"] is not None
        assert body["items"][0]["title"] == "测试快讯"
        assert body["items"][0]["related_codes"] == ["600519"]
    finally:
        cm._connector.fetch_market_news = original


def test_market_sectors_mock_returns_board(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    original = cm._connector.fetch_sector_board

    async def mock_board(top_n: int = 10) -> dict[str, Any]:
        row = {
            "name": "钼", "code": "BK1623", "pct_chg": 10.0,
            "up_count": 2, "down_count": 0,
            "leader": "金钼股份", "leader_code": "601958", "leader_pct_chg": 10.0,
        }
        return {"total_boards": 496, "top": [row], "bottom": [row]}

    cm._connector.fetch_sector_board = mock_board
    try:
        r = client.get("/market/sectors", headers=auth_headers, params={"top_n": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["total_boards"] == 496
        assert body["top"][0]["name"] == "钼"
        assert body["top"][0]["leader"] == "金钼股份"
    finally:
        cm._connector.fetch_sector_board = original


def test_market_moneyflow_mock_includes_note(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """北向口径 note 必须透传——估算数值被当官方实时数据是误读。"""
    original = cm._connector.fetch_moneyflow

    async def mock_flow() -> dict[str, Any]:
        return {
            "as_of_time": "15:00",
            "hgt_net_yi_cny": -9.28,
            "sgt_net_yi_cny": -31.1,
            "north_net_yi_cny": -40.38,
            "series_sample": [{"time": "09:30", "hgt": -1.0, "sgt": -2.0}],
            "note": cm.NORTHBOUND_NOTE,
        }

    cm._connector.fetch_moneyflow = mock_flow
    try:
        r = client.get("/market/moneyflow", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["north_net_yi_cny"] == -40.38
        assert "估算口径" in body["note"]
    finally:
        cm._connector.fetch_moneyflow = original


def test_market_movers_mock_returns_tags(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    original = cm._connector.fetch_strong_stocks

    async def mock_movers(limit: int = 30) -> list[dict[str, Any]]:
        return [
            {
                "code": "688163", "name": "赛伦生物",
                "reason": "抗蛇毒血清+独家品种",
                "tags": ["抗蛇毒血清", "独家品种"], "date": "2026-06-12",
            }
        ]

    cm._connector.fetch_strong_stocks = mock_movers
    try:
        r = client.get("/market/movers", headers=auth_headers, params={"limit": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["items"][0]["tags"] == ["抗蛇毒血清", "独家品种"]
    finally:
        cm._connector.fetch_strong_stocks = original


def test_market_source_failure_returns_502(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """源站失败 → 502 MARKET_DATA_UNAVAILABLE，不静默空（事故模式防回归）。"""
    original = cm._connector.fetch_market_news

    async def mock_boom(limit: int = 20) -> list[dict[str, Any]]:
        raise CnMarketError("eastmoney blocked: HTTP 403")

    cm._connector.fetch_market_news = mock_boom
    try:
        r = client.get("/market/news", headers=auth_headers)
        assert r.status_code == 502
        assert r.json()["code"] == "MARKET_DATA_UNAVAILABLE"
    finally:
        cm._connector.fetch_market_news = original


# ── connector 归一化层 ───────────────────────────────────────────────


def _patch_get_json(conn: CnMarketConnector, payload: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(host_key: str, url: str, *, params=None, headers=None) -> dict[str, Any]:
        calls.append({"host": host_key, "url": url, "params": params})
        return payload

    conn._get_json = fake  # type: ignore[method-assign]
    return calls


async def test_news_normalizes_beijing_time_to_utc() -> None:
    """东财 showTime 是北京时间字符串，必须转 UTC（+08:00 → -8h）。"""
    conn = CnMarketConnector()
    _patch_get_json(
        conn,
        {
            "data": {
                "fastNewsList": [
                    {
                        "title": "t", "summary": "s",
                        "showTime": "2026-06-12 14:48:09",
                        "stockList": [{"code": "600519"}, {"name": "无code"}],
                    }
                ]
            }
        },
    )
    items = await conn.fetch_market_news(limit=5)
    assert items[0]["published_at"] == "2026-06-12T06:48:09+00:00"
    assert items[0]["related_codes"] == ["600519"]
    await conn.close()


async def test_sector_board_two_pages_top_and_bottom() -> None:
    """东财 pz 单页上限 100 → 涨跌两端各拉一页（po=1 降序 / po=0 升序），总数读 data.total。"""
    conn = CnMarketConnector()
    diff = [
        {"f14": f"板块{i}", "f12": f"BK{i}", "f3": float(i), "f104": 1, "f105": 0,
         "f128": "领涨", "f140": "000001", "f136": float(i)}
        for i in range(2)
    ]
    calls = _patch_get_json(conn, {"data": {"total": 496, "diff": diff}})
    out = await conn.fetch_sector_board(top_n=2)
    assert out["total_boards"] == 496
    assert len(calls) == 2
    assert [c["params"]["po"] for c in calls] == ["1", "0"]  # 降序页 + 升序页
    assert out["top"][0]["name"] == "板块0"
    assert out["bottom"][0]["pct_chg"] == 0.0
    await conn.close()


async def test_moneyflow_desynced_components_no_merge() -> None:
    """两分量更新不同步：各自给最新值，north 不合并（避免拼接错位时点 §3.1），
    as_of_time 取两者都更新到的最晚共同时刻（min，非 max）。"""
    conn = CnMarketConnector()
    times = [f"09:{i:02d}" for i in range(60)]
    hgt = [float(-i) for i in range(59)] + [None]  # hgt 停在 idx 58
    sgt = [float(-2 * i) for i in range(60)]  # sgt 更新到 idx 59
    _patch_get_json(conn, {"time": times, "hgt": hgt, "sgt": sgt})
    out = await conn.fetch_moneyflow()
    assert out["hgt_net_yi_cny"] == -58.0  # 各自最新仍给
    assert out["sgt_net_yi_cny"] == -118.0
    assert out["north_net_yi_cny"] is None  # 不同步 → 不合并
    assert out["as_of_time"] == "09:58"  # min(58, 59) 共同时刻，非 max 的 09:59
    assert out["series_sample"][0]["time"] == "09:00"
    assert "估算口径" in out["note"]
    await conn.close()


async def test_moneyflow_synced_components_merge() -> None:
    """两分量更新到同一时刻：north 合并为 hgt+sgt。"""
    conn = CnMarketConnector()
    times = [f"09:{i:02d}" for i in range(60)]
    hgt = [float(-i) for i in range(60)]
    sgt = [float(-2 * i) for i in range(60)]
    _patch_get_json(conn, {"time": times, "hgt": hgt, "sgt": sgt})
    out = await conn.fetch_moneyflow()
    assert out["hgt_net_yi_cny"] == -59.0
    assert out["sgt_net_yi_cny"] == -118.0
    assert out["north_net_yi_cny"] == -177.0  # 同步 → 合并
    assert out["as_of_time"] == "09:59"
    await conn.close()


async def test_strong_stocks_splits_reason_tags() -> None:
    conn = CnMarketConnector()
    _patch_get_json(
        conn,
        {"errocode": 0, "data": [
            {"code": "688163", "name": "赛伦生物", "reason": "抗蛇毒血清+独家品种", "date": "2026-06-12"}
        ]},
    )
    items = await conn.fetch_strong_stocks(limit=10)
    assert items[0]["tags"] == ["抗蛇毒血清", "独家品种"]
    await conn.close()


async def test_connector_raises_on_unexpected_shape() -> None:
    """源站改版（字段缺失）必须上抛，不静默空。"""
    conn = CnMarketConnector()
    _patch_get_json(conn, {"data": {}})
    with pytest.raises(CnMarketError):
        await conn.fetch_market_news()
    with pytest.raises(CnMarketError):
        await conn.fetch_sector_board()
    await conn.close()


async def test_connector_caches_success() -> None:
    conn = CnMarketConnector()
    calls = _patch_get_json(
        conn,
        {"data": {"fastNewsList": [{"title": "t", "summary": "", "showTime": "", "stockList": []}]}},
    )
    await conn.fetch_market_news(limit=5)
    await conn.fetch_market_news(limit=5)
    assert len(calls) == 1  # 第二次走 60s 缓存
    await conn.close()


async def test_get_json_serializes_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """同 host 两次请求间隔 ≥ min_interval（防封铁律）。"""
    conn = CnMarketConnector()
    conn._min_interval = 0.2
    conn._cache_ttl = 0  # 关缓存逼第二次真打

    class FakeResp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"data": {"fastNewsList": []}}

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)
        await real_sleep(0)

    async def fake_get(url: str, params=None, headers=None) -> FakeResp:
        return FakeResp()

    monkeypatch.setattr(conn._client, "get", fake_get)
    monkeypatch.setattr(cm.asyncio, "sleep", fake_sleep)
    # 直接调 _get_json 两次验证限速
    await conn._get_json("h", "http://example.com", params=None, headers=None)
    await conn._get_json("h", "http://example.com", params=None, headers=None)
    assert sleeps, "第二次请求应触发限速 sleep"
    assert sleeps[0] >= 0.1  # min_interval 剩余 + 0.1~0.5 抖动
    await conn.close()

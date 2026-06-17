"""``GET /archetypes`` 端点测试（ADR-0051 D1）。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_archetypes_requires_auth(client: TestClient) -> None:
    r = client.get("/archetypes")
    assert r.status_code == 401


def test_archetypes_lists_all(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/archetypes", headers=auth_headers)
    assert r.status_code == 200
    names = [a["name"] for a in r.json()["archetypes"]]
    assert names == [
        "momentum_trend",
        "mean_reversion",
        "volatility_contraction",
        "multi_factor_combine",
        "single_factor_assistive",
    ]
    # 每条带可跑源码 + 元数据
    first = r.json()["archetypes"][0]
    assert first["code"].startswith("class ")
    assert first["applies_to_kinds"]
    assert first["params"]


def test_archetypes_ranks_by_factor_kind(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/archetypes", params={"factor_kinds": "mean_reversion"}, headers=auth_headers
    )
    assert r.status_code == 200
    archs = r.json()["archetypes"]
    assert archs[0]["name"] == "mean_reversion"
    # 只排序不过滤
    assert len(archs) == 5

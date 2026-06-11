"""因子候选池测试（D-12 · 因子发现 L1·P2）：503 降级 / 表达式先验 / 注册表→catalog 链路。

DB round-trip（propose→review→registered）属集成范畴，由 e2e 手动验收；这里覆盖
不依赖 Postgres 的全部行为面。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from inalpha_factor import custom_registry
from inalpha_factor.adapters import CustomAdapter
from inalpha_factor.config import get_factor_settings
from inalpha_factor.engine import FactorEngine
from inalpha_factor.main import app

from .conftest import make_ohlcv


@pytest.fixture
def client_no_db() -> TestClient:
    """不跑 lifespan（db_ready 缺省 False）——模拟 DB 不可用。"""
    app.state.db_ready = False
    return TestClient(app)


def test_candidates_503_without_db(client_no_db: TestClient) -> None:
    r = client_no_db.get("/candidates")
    assert r.status_code == 503
    assert r.json()["code"] == "FACTOR_DB_UNAVAILABLE"

    r = client_no_db.post(
        "/candidates",
        json={
            "expression": "Mean($close, 20)",
            "hypothesis": "均线水平本身不构成假设，但这是合法表达式用于测 503",
        },
    )
    assert r.status_code == 503


def test_propose_validates_expression_before_db(client_no_db: TestClient) -> None:
    """非法表达式即使 DB 不可用也返 400（可改写），不是 503（不可操作）。"""
    r = client_no_db.post(
        "/candidates",
        json={
            "expression": "Ref($close, -5)",
            "hypothesis": "这是一个想偷未来数据的表达式，应该在审计层被拒绝",
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "FACTOR_EXPRESSION_INVALID"


def test_propose_requires_hypothesis(client_no_db: TestClient) -> None:
    """经济学故事门：hypothesis < 20 字符 → FastAPI 422。"""
    r = client_no_db.post(
        "/candidates",
        json={"expression": "Mean($close, 20)", "hypothesis": "太短"},
    )
    # pydantic 校验失败（共享 error handler 统一成 4xx 包装）
    assert r.status_code in (400, 422)


def test_timing_score_catalog_unaffected_without_db(client_no_db: TestClient) -> None:
    """DB 不可用只降级 candidates；catalog 照常。"""
    r = client_no_db.get("/catalog")
    assert r.status_code == 200


# ── 注册表 → adapter → engine catalog 链路（monkeypatch DB 层）──────────


@pytest.fixture
def registered_factor(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """往注册表塞一个已注册因子（绕 DB：直接 patch storage.list_registered）。"""
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "expression": "($close - Ref($close, 5)) / Ref($close, 5)",
        "expression_hash": "abcdef0123456789",
        "name": "5 根动量（测试）",
        "hypothesis": "动量效应：近期上涨的标的短期内倾向继续上涨",
    }

    async def _fake_list_registered(_conn: object) -> list[dict[str, Any]]:
        return [row]

    class _FakeConn:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(custom_registry, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        custom_registry.candidates_store, "list_registered", _fake_list_registered
    )
    custom_registry.clear()
    return row


async def test_registry_refresh_and_adapter_compute(registered_factor: dict[str, Any]) -> None:
    n = await custom_registry.refresh()
    assert n == 1

    adapter = CustomAdapter()
    specs = adapter.specs()
    assert [s.factor_id for s in specs] == ["custom.abcdef0123456789"]
    assert specs[0].name == "5 根动量（测试）"

    df = make_ohlcv(100)
    series = adapter.compute(df)
    want = df["close"].astype(float).pct_change(5)
    got = series["custom.abcdef0123456789"]
    # (c-Ref)/Ref 与 pct_change 数学等价但浮点路径不同 → allclose 而非 equals
    assert np.allclose(got.dropna(), want.dropna())
    assert got.isna().equals(want.isna())

    custom_registry.clear()
    assert adapter.specs() == []


async def test_registered_factor_enters_engine_catalog(
    registered_factor: dict[str, Any],
) -> None:
    """注册即生产：custom 因子自动进 engine catalog / computable ids。"""
    await custom_registry.refresh()
    try:
        eng = FactorEngine(get_factor_settings())
        ids = {s.factor_id for s in eng.catalog()}
        assert "custom.abcdef0123456789" in ids
        assert "custom.abcdef0123456789" in eng._computable_ids("1h")
        assert eng.sources()["custom"] is True
    finally:
        custom_registry.clear()


async def test_registry_skips_invalid_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕过 API 直插 DB 的非法表达式：跳过 + 不拖垮其余注册因子。"""
    rows = [
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "expression": "Ref($close, -1)",  # 非法：负 lag
            "expression_hash": "badbadbadbad0000",
            "name": None,
            "hypothesis": "x" * 20,
        },
        {
            "id": "00000000-0000-0000-0000-000000000003",
            "expression": "Mean($close, 10)",
            "expression_hash": "00000000000000aa",
            "name": None,
            "hypothesis": "x" * 20,
        },
    ]

    async def _fake_list_registered(_conn: object) -> list[dict[str, Any]]:
        return rows

    class _FakeConn:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(custom_registry, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        custom_registry.candidates_store, "list_registered", _fake_list_registered
    )
    custom_registry.clear()
    try:
        n = await custom_registry.refresh()
        assert n == 1  # 非法那条被跳过
        assert custom_registry.get_registered()[0].spec.factor_id == "custom.00000000000000aa"
    finally:
        custom_registry.clear()

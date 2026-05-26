"""``/risk/*`` API 测试（ADR-0006 Slice 7）。

3 个端点 + 模块级 config cache + 简单 noop mock。

`integration` 标记：用 conftest `client` fixture（启 lifespan + DB pool）。
真 DB 跑 alembic 0006 后端到端可用；没跑也能跑 `/risk/rules`（只读模块缓存）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.api.risk import _reset_config_cache
from inalpha_paper.execution.risk_rules import RiskRulesConfig

pytestmark = pytest.mark.integration


_RISK_LOCKS_DDL = """
CREATE TABLE IF NOT EXISTS risk_locks (
    id              BIGSERIAL PRIMARY KEY,
    scope           VARCHAR(16) NOT NULL
                    CHECK (scope IN ('global', 'market', 'symbol')),
    market          VARCHAR(64),
    symbol          VARCHAR(128),
    side            VARCHAR(8) NOT NULL DEFAULT '*'
                    CHECK (side IN ('long', 'short', '*')),
    rule_name       VARCHAR(64) NOT NULL,
    reason          TEXT NOT NULL,
    locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_until    TIMESTAMPTZ NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    unlocked_at     TIMESTAMPTZ,
    unlocked_by     TEXT,
    unlock_reason   TEXT
)
"""


@pytest.fixture(autouse=True)
def _reset_risk_config() -> Any:
    """每个测试前后 reset config cache（避免互相污染）。"""
    _reset_config_cache()
    yield
    _reset_config_cache()


@pytest_asyncio.fixture
async def risk_locks_table(client: TestClient) -> AsyncIterator[None]:
    """Ensure `risk_locks` 表存在 + 测试前后清空（独立于 alembic 命令行）。

    依赖 client fixture 拉起 DB pool。CREATE TABLE IF NOT EXISTS 幂等。
    DDL 必须与 `infra/migrations/versions/0006_risk_locks.py` 保持一致。
    """
    del client  # 仅作 lifespan 依赖
    async with get_conn() as conn:
        await conn.execute(_RISK_LOCKS_DDL)
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()
    yield
    async with get_conn() as conn:
        await conn.execute("DELETE FROM risk_locks")
        await conn.commit()


# ─── GET /risk/rules ───


def test_get_rules_returns_default_config(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """默认从 services/paper/configs/risk_rules.toml 加载 5 件套。"""
    r = client.get("/risk/rules", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["starting_balance"] == 10_000.0
    rule_names = [rule["name"] for rule in body["rules"]]
    assert set(rule_names) == {
        "CooldownRule",
        "LowProfitRule",
        "MaxDrawdownRule",
        "StoplossGuardRule",
        "MarketHoursRule",
    }
    for rule in body["rules"]:
        assert isinstance(rule["short_desc"], str)
        assert len(rule["short_desc"]) > 0


def test_get_rules_disabled(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置 disabled 时返回空 rules 列表。"""
    from inalpha_paper.api import risk as risk_module

    monkeypatch.setattr(
        risk_module, "_get_config", lambda: RiskRulesConfig(enabled=False)
    )
    r = client.get("/risk/rules", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["rules"] == []


def test_get_rules_requires_auth(client: TestClient) -> None:
    r = client.get("/risk/rules")
    assert r.status_code in (401, 403)


# ─── GET /risk/locks ───


def test_get_locks_empty_by_default(
    client: TestClient, auth_headers: dict[str, str], risk_locks_table: None
) -> None:
    """DB 没数据时返空 list。"""
    del risk_locks_table  # ensure 表 + 清空
    r = client.get("/risk/locks", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"locks": []}


@pytest.mark.asyncio
async def test_get_locks_returns_inserted_rows(
    client: TestClient, auth_headers: dict[str, str], risk_locks_table: None
) -> None:
    """直接往 DB insert 一行，API 应返回。"""
    del risk_locks_table
    from inalpha_paper.storage import risk_locks as locks_store

    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="symbol",
            rule_name="TestRule",
            reason="API 测试",
            locked_until=until,
            market="binance",
            symbol="BTC/USDT@binance",
            side="*",
        )
        await conn.commit()

    r = client.get("/risk/locks", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["locks"]) == 1
    lock = body["locks"][0]
    assert lock["id"] == lock_id
    assert lock["scope"] == "symbol"
    assert lock["market"] == "binance"
    assert lock["symbol"] == "BTC/USDT@binance"
    assert lock["rule_name"] == "TestRule"


@pytest.mark.asyncio
async def test_get_locks_filter_by_scope(
    client: TestClient, auth_headers: dict[str, str], risk_locks_table: None
) -> None:
    """scope=global 过滤。"""
    del risk_locks_table
    from inalpha_paper.storage import risk_locks as locks_store

    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        await locks_store.insert(
            conn, scope="global", rule_name="R1", reason="g", locked_until=until,
        )
        await locks_store.insert(
            conn, scope="symbol", rule_name="R2", reason="s", locked_until=until,
            market="binance", symbol="BTC/USDT@binance",
        )
        await conn.commit()

    r = client.get("/risk/locks?scope=global", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["locks"]) == 1
    assert body["locks"][0]["scope"] == "global"


# ─── POST /risk/locks/{id}/unlock ───


@pytest.mark.asyncio
async def test_unlock_success(
    client: TestClient, auth_headers: dict[str, str], risk_locks_table: None
) -> None:
    del risk_locks_table
    from inalpha_paper.storage import risk_locks as locks_store

    until = datetime.now(UTC) + timedelta(hours=1)
    async with get_conn() as conn:
        lock_id = await locks_store.insert(
            conn,
            scope="symbol",
            rule_name="R",
            reason="x",
            locked_until=until,
            market="binance",
            symbol="BTC/USDT@binance",
        )
        await conn.commit()

    r = client.post(
        f"/risk/locks/{lock_id}/unlock",
        json={"reason": "测试解锁"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # active=FALSE 后 list 应为空
    r2 = client.get("/risk/locks", headers=auth_headers)
    assert r2.json() == {"locks": []}


def test_unlock_nonexistent_returns_404(
    client: TestClient, auth_headers: dict[str, str], risk_locks_table: None
) -> None:
    del risk_locks_table
    r = client.post(
        "/risk/locks/999999/unlock",
        json={"reason": "测试"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_unlock_requires_reason(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """空 reason 被 Pydantic min_length=1 拒（项目 install_error_handler 把 422 转 400）。"""
    r = client.post(
        "/risk/locks/1/unlock", json={"reason": ""}, headers=auth_headers
    )
    assert r.status_code == 400

"""测试 fixture。

集成测试用真实 DB（docker compose 起的 postgres 在 localhost:5433）。
不需要真实 Binance —— ``BinanceConnector`` 在测试里 mock 掉。
"""
from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from quant_lab_shared.config import get_settings
from quant_lab_shared.db import close_pool, init_pool

# === 测试 secret + token ===

TEST_JWT_SECRET = "test-secret-do-not-use-in-prod-please-and-thank-you"


def make_test_token(sub: str = "test-user", email: str = "t@e.st") -> str:
    payload = {"sub": sub, "email": email, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_token()}"}


# === DB fixtures ===


@pytest.fixture(scope="session", autouse=True)
def _ensure_env() -> None:
    """确保 DATABASE_URL / JWT_SECRET 在测试环境就位。"""
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://quant:devpass@localhost:5433/quant_lab",
    )
    os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
    # 清缓存让 get_settings / get_data_settings 拿到新环境
    get_settings.cache_clear()


@pytest.fixture
async def db_pool() -> AsyncIterator[None]:
    """每个测试函数前后 init / close pool。"""
    settings = get_settings()
    await init_pool(settings.database_url)
    try:
        yield
    finally:
        await close_pool()


@pytest.fixture
def venue_symbol_tf() -> tuple[str, str, str]:
    """每个测试用独一无二的 symbol，避免互相污染 bars 表。"""
    return ("test-venue", f"TEST/USDT-{uuid4().hex[:8]}", "1h")


# === FastAPI app fixture（含 dependency overrides） ===


def _make_app() -> Any:
    """重新 import main 拿到 fresh app（避免 lifespan 全局状态干扰）。"""
    from quant_lab_data.main import app

    return app


@pytest.fixture
async def app_with_overrides() -> AsyncIterator[Any]:
    """启动 app 的 lifespan（DB pool + connector）+ 注入 mock connector。"""
    app = _make_app()

    # mock connector
    from quant_lab_data.connectors.binance import BinanceConnector, get_connector

    class MockBinanceConnector(BinanceConnector):
        def __init__(self) -> None:
            pass

        async def fetch_bars(
            self,
            symbol: str,
            timeframe: str,
            since: datetime,
            limit: int = 1000,
        ) -> list[tuple[datetime, float, float, float, float, float]]:
            # 生成 5 根递增的假 bar
            start = since.replace(microsecond=0)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            return [
                (
                    start + timedelta(hours=i),
                    100.0 + i,
                    101.0 + i,
                    99.0 + i,
                    100.5 + i,
                    1000.0 + i * 10,
                )
                for i in range(5)
            ]

        async def close(self) -> None:
            pass

    app.dependency_overrides[get_connector] = lambda: MockBinanceConnector()

    # 跑 lifespan（启动 DB pool 等）
    async with app.router.lifespan_context(app):
        yield app

    app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_overrides: Any) -> TestClient:
    return TestClient(app_with_overrides)

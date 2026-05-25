"""pytest fixture：测试 secret + auth headers + 一个最小 BarResponse builder。

D-8b 起：API 路由用 DBConn dependency，需要在 TestClient 之前起 lifespan
（init_pool）。``client`` fixture 用 ``app.router.lifespan_context`` 拉起后给 TestClient。
"""
from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.config import get_settings

TEST_JWT_SECRET = "test-secret-do-not-use-in-prod-please-and-thank-you"


def make_test_token(sub: str = "test-user", email: str = "t@e.st") -> str:
    return jwt.encode(
        {"sub": sub, "email": email, "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_token()}"}


@pytest.fixture(scope="session", autouse=True)
def _ensure_env() -> None:
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://quant:devpass@localhost:5433/inalpha",
    )
    os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
    os.environ.setdefault("DATA_SERVICE_URL", "http://data-mock.test")
    # Swarm S1：默认关 backtest ProcessPool。每个 TestClient lifespan 都 spawn 6
    # worker + import numpy 每次 ~2s，会拖慢整套测试到 130s+。runner._run_engine
    # 自动回落同进程跑，业务路径覆盖率不变。pool 真路径由 test_pool.py 显式打开。
    os.environ.setdefault("PAPER_POOL_DISABLED", "1")
    get_settings.cache_clear()


def make_bar_row(ts_iso: str, close: float = 100.0) -> dict[str, Any]:
    """合成一行 data-service 风格的 BarResponse JSON。"""
    return {
        "ts": ts_iso,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
    }


# ────────────────────────────────────────────────────────────────────
# D-8b：DB-backed app fixture（lifespan 拉起 + truncate 隔离）
# ────────────────────────────────────────────────────────────────────


def _make_app() -> Any:
    """每次返回 fresh app（避免 lifespan 全局状态干扰）。"""
    from inalpha_paper.main import app

    return app


@pytest_asyncio.fixture
async def app_with_lifespan() -> AsyncIterator[Any]:
    """启 app 的 lifespan（连 DB pool）。"""
    app = _make_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
def client(app_with_lifespan: Any) -> TestClient:
    return TestClient(app_with_lifespan)


def fresh_account_token(prefix: str = "test") -> tuple[str, str]:
    """给每个测试一个独立的 sub（user），避免互相污染。

    Returns ``(sub, jwt_token)``。
    """
    sub = f"{prefix}-{uuid4().hex[:12]}"
    return sub, make_test_token(sub=sub)


@pytest.fixture
def fresh_user() -> dict[str, str]:
    """每个测试拿独立 user_id 的 auth header。"""
    sub, token = fresh_account_token()
    return {"sub": sub, "Authorization": f"Bearer {token}", "_token": token}

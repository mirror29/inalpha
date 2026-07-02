"""POST /auth/login 端到端。

验证:

1. 正确邮箱 + 密码 → 200 + {subject, email, roles}
2. 错误密码 → 401 INVALID_CREDENTIALS
3. 不存在的邮箱 → 401 INVALID_CREDENTIALS(与密码错同一 code,不泄露账号是否存在)
4. 邮箱大小写不敏感
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

pytestmark = pytest.mark.integration

_TEST_EMAIL = "login-test@example.com"
_TEST_PASSWORD = "correct-horse-battery-staple"
_TEST_SUBJECT = "user:login-test-fixture"


@pytest_asyncio.fixture
async def seeded_user(app_with_lifespan: object) -> AsyncIterator[None]:
    """往 users 表插一个已知 argon2 哈希的测试用户,测试后删。"""
    password_hash = PasswordHasher().hash(_TEST_PASSWORD)
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users (subject, email, password_hash, roles) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (subject) DO UPDATE SET "
                "email = EXCLUDED.email, password_hash = EXCLUDED.password_hash",
                (_TEST_SUBJECT, _TEST_EMAIL, password_hash, ["trader"]),
            )
            await conn.commit()
    try:
        yield
    finally:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM users WHERE subject = %s", (_TEST_SUBJECT,))
                await conn.commit()


def test_login_success(client: TestClient, seeded_user: None) -> None:
    resp = client.post(
        "/auth/login",
        json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == _TEST_SUBJECT
    assert body["email"] == _TEST_EMAIL
    assert body["roles"] == ["trader"]


def test_login_email_case_insensitive(client: TestClient, seeded_user: None) -> None:
    resp = client.post(
        "/auth/login",
        json={"email": _TEST_EMAIL.upper(), "password": _TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["subject"] == _TEST_SUBJECT


def test_login_wrong_password(client: TestClient, seeded_user: None) -> None:
    resp = client.post(
        "/auth/login",
        json={"email": _TEST_EMAIL, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email(client: TestClient) -> None:
    resp = client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401
    # 与密码错同一 code —— 不泄露账号是否存在。
    assert resp.json()["code"] == "INVALID_CREDENTIALS"

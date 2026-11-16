"""注册 waitlist 与管理员审核端到端测试。"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import anyio
import jwt
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.api import auth as auth_mod

pytestmark = pytest.mark.integration

_ADMIN_SUBJECT = "user:waitlist-admin"
_ADMIN_EMAIL = "admin-waitlist@example.com"
_APPLICANT_EMAIL = "applicant-waitlist@example.com"
_OPS_SUBJECT = "user:ops-preserve"
_OPS_EMAIL = "ops-preserve@example.com"
_PASSWORD = "correct-horse-battery-staple"
_JWT_SECRET = "test-secret-do-not-use-in-prod-please-and-thank-you"
_SERVICE_ROOT = os.fspath(Path(__file__).resolve().parents[1])


def _token(subject: str, email: str = "test@example.com") -> str:
    return jwt.encode(
        {"sub": subject, "email": email, "exp": int(time.time()) + 3600},
        _JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def _reset_auth_throttles() -> Iterator[None]:
    """注册与登录节流是进程内状态，逐用例清理。"""
    auth_mod._login_failures.clear()
    auth_mod._registration_attempts.clear()
    auth_mod._registration_events.clear()
    yield
    auth_mod._login_failures.clear()
    auth_mod._registration_attempts.clear()
    auth_mod._registration_events.clear()


@pytest_asyncio.fixture
async def waitlist_state(app_with_lifespan: object) -> AsyncIterator[None]:
    """种管理员并清理本测试创建的申请。"""
    password_hash = PasswordHasher().hash(_PASSWORD)
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users (subject, email, password_hash, roles, access_status)
                VALUES (%s, %s, %s, ARRAY['admin'], 'active')
                ON CONFLICT (subject) DO UPDATE SET
                    email = EXCLUDED.email,
                    password_hash = EXCLUDED.password_hash,
                    roles = EXCLUDED.roles,
                    access_status = EXCLUDED.access_status
                """,
                (_ADMIN_SUBJECT, _ADMIN_EMAIL, password_hash),
            )
            await cur.execute("DELETE FROM users WHERE lower(email) = %s", (_APPLICANT_EMAIL,))
            await conn.commit()
    try:
        yield
    finally:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM users WHERE subject IN (%s, %s) OR lower(email) = %s",
                    (_ADMIN_SUBJECT, _OPS_SUBJECT, _APPLICANT_EMAIL),
                )
                await conn.commit()


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(_ADMIN_SUBJECT, _ADMIN_EMAIL)}"}


def test_register_pending_then_admin_approves(
    client: TestClient,
    waitlist_state: None,
) -> None:
    register = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL.upper(),
            "display_name": "Test Applicant",
            "application_note": "I want to validate factor metrics.",
        },
    )
    assert register.status_code == 202, register.text
    assert register.json() == {"accepted": True}

    waitlist = client.get("/auth/waitlist", headers=_admin_headers())
    assert waitlist.status_code == 200, waitlist.text
    applicant = next(user for user in waitlist.json()["users"] if user["email"] == _APPLICANT_EMAIL)
    assert applicant["display_name"] == "Test Applicant"
    assert "password_hash" not in applicant

    approved = client.post(
        f"/auth/waitlist/{applicant['subject']}/review",
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["access_status"] == "invited"
    assert approved.json()["email"] == _APPLICANT_EMAIL
    activation_token = approved.json()["activation_token"]
    assert isinstance(activation_token, str)

    invited_login = client.post(
        "/auth/login",
        json={
            "email": _APPLICANT_EMAIL,
            "password": _PASSWORD,
        },
    )
    assert invited_login.status_code == 401
    assert invited_login.json()["code"] == "INVALID_CREDENTIALS"

    activated = client.post(
        "/auth/activate",
        json={"token": activation_token, "password": _PASSWORD},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json() == {"activated": True}

    active_login = client.post(
        "/auth/login", json={"email": _APPLICANT_EMAIL, "password": _PASSWORD}
    )
    assert active_login.status_code == 200, active_login.text
    assert active_login.json()["email"] == _APPLICANT_EMAIL

    async def read_actions() -> list[str]:
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT action FROM user_access_events WHERE target_subject = %s "
                    "ORDER BY id",
                    (applicant["subject"],),
                )
                return [row["action"] for row in await cur.fetchall()]

    assert anyio.run(read_actions) == ["approved", "activated"]


def test_non_admin_cannot_read_or_review_waitlist(client: TestClient, waitlist_state: None) -> None:
    headers = {"Authorization": f"Bearer {_token('ordinary-user')}"}
    listed = client.get("/auth/waitlist", headers=headers)
    assert listed.status_code == 403
    assert listed.json()["code"] == "ADMIN_REQUIRED"
    reviewed = client.post(
        "/auth/waitlist/user:any/review",
        headers=headers,
        json={"decision": "approve"},
    )
    assert reviewed.status_code == 403


def test_duplicate_registration_does_not_replace_existing_application(
    client: TestClient, waitlist_state: None
) -> None:
    first = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL,
            "display_name": "Original",
        },
    )
    second = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL,
            "display_name": "Replacement",
        },
    )
    assert first.status_code == second.status_code == 202
    waitlist = client.get("/auth/waitlist", headers=_admin_headers()).json()["users"]
    applicant = next(user for user in waitlist if user["email"] == _APPLICANT_EMAIL)
    assert applicant["display_name"] == "Original"


def test_registration_rate_limit_precedes_argon2_work(
    client: TestClient, waitlist_state: None
) -> None:
    for index in range(auth_mod._REGISTER_MAX_PER_EMAIL):
        response = client.post(
            "/auth/register",
            json={
                "email": _APPLICANT_EMAIL,
                "display_name": f"Attempt {index}",
            },
        )
        assert response.status_code == 202
    limited = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL,
            "display_name": "Too many",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["code"] == "REGISTER_RATE_LIMITED"


def test_global_registration_rate_limit_precedes_argon2_work(
    client: TestClient, waitlist_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_mod._registration_events.extend([time.monotonic()] * auth_mod._REGISTER_GLOBAL_MAX)

    def unexpected_hash(_password: str) -> str:
        raise AssertionError("rate-limited registration must not run Argon2")

    monkeypatch.setattr(auth_mod, "_hash_password", unexpected_hash)
    limited = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL,
            "display_name": "Global limit",
        },
    )

    assert limited.status_code == 429
    assert limited.json()["code"] == "REGISTER_RATE_LIMITED"


@pytest.mark.parametrize(
    ("email", "display_name", "code"),
    [
        ("not-an-email", "Applicant", "INVALID_EMAIL"),
        ("a@b@c.com", "Applicant", "INVALID_EMAIL"),
        (_APPLICANT_EMAIL, "   ", "INVALID_DISPLAY_NAME"),
    ],
)
def test_registration_rejects_invalid_identity_fields(
    client: TestClient,
    waitlist_state: None,
    email: str,
    display_name: str,
    code: str,
) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "display_name": display_name,
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == code


def test_admin_rejects_application_and_repeat_review_conflicts(
    client: TestClient, waitlist_state: None
) -> None:
    register = client.post(
        "/auth/register",
        json={
            "email": _APPLICANT_EMAIL,
            "display_name": "Rejected Applicant",
        },
    )
    assert register.status_code == 202
    waitlist = client.get("/auth/waitlist", headers=_admin_headers()).json()["users"]
    applicant = next(user for user in waitlist if user["email"] == _APPLICANT_EMAIL)

    rejected = client.post(
        f"/auth/waitlist/{applicant['subject']}/review",
        headers=_admin_headers(),
        json={"decision": "reject"},
    )
    repeated = client.post(
        f"/auth/waitlist/{applicant['subject']}/review",
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    login = client.post("/auth/login", json={"email": _APPLICANT_EMAIL, "password": _PASSWORD})

    assert rejected.status_code == 200
    assert rejected.json()["access_status"] == "rejected"
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "WAITLIST_ALREADY_REVIEWED"
    assert login.status_code == 401


def test_activation_token_is_one_time(client: TestClient, waitlist_state: None) -> None:
    assert (
        client.post(
            "/auth/register",
            json={"email": _APPLICANT_EMAIL, "display_name": "One Time"},
        ).status_code
        == 202
    )
    applicant = next(
        user
        for user in client.get("/auth/waitlist", headers=_admin_headers()).json()["users"]
        if user["email"] == _APPLICANT_EMAIL
    )
    approved = client.post(
        f"/auth/waitlist/{applicant['subject']}/review",
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    token = approved.json()["activation_token"]

    first = client.post("/auth/activate", json={"token": token, "password": _PASSWORD})
    second = client.post("/auth/activate", json={"token": token, "password": _PASSWORD})

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["code"] == "INVALID_ACTIVATION_TOKEN"


def test_activation_argon2_gate_rejects_without_queueing(
    client: TestClient,
    waitlist_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """激活槽位饱和时在查询令牌前立即 429，避免堆积过期哈希任务。"""
    monkeypatch.setattr(auth_mod, "_activation_hash_slots", anyio.Semaphore(0))

    response = client.post(
        "/auth/activate",
        json={"token": "x" * 43, "password": _PASSWORD},
    )

    assert response.status_code == 429
    assert response.json()["code"] == "ACTIVATION_BUSY"


def test_admin_can_rotate_an_unclaimed_activation_token(
    client: TestClient, waitlist_state: None
) -> None:
    assert (
        client.post(
            "/auth/register",
            json={"email": _APPLICANT_EMAIL, "display_name": "Rotate Token"},
        ).status_code
        == 202
    )
    applicant = next(
        user
        for user in client.get("/auth/waitlist", headers=_admin_headers()).json()["users"]
        if user["email"] == _APPLICANT_EMAIL
    )
    first = client.post(
        f"/auth/waitlist/{applicant['subject']}/review",
        headers=_admin_headers(),
        json={"decision": "approve"},
    ).json()["activation_token"]
    invited = next(
        user
        for user in client.get("/auth/waitlist", headers=_admin_headers()).json()["users"]
        if user["email"] == _APPLICANT_EMAIL
    )
    second = client.post(
        f"/auth/waitlist/{invited['subject']}/review",
        headers=_admin_headers(),
        json={
            "decision": "approve",
            "expected_reviewed_at": invited["reviewed_at"],
        },
    ).json()["activation_token"]

    assert invited["access_status"] == "invited"
    assert first != second
    assert (
        client.post("/auth/activate", json={"token": first, "password": _PASSWORD}).status_code
        == 400
    )
    assert (
        client.post("/auth/activate", json={"token": second, "password": _PASSWORD}).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_create_user_update_preserves_roles_and_access_status(
    waitlist_state: None,
) -> None:
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users (subject, email, password_hash, roles, access_status)
                VALUES (%s, %s, %s, ARRAY['admin'], 'rejected')
                """,
                (_OPS_SUBJECT, _OPS_EMAIL, PasswordHasher().hash(_PASSWORD)),
            )
            await conn.commit()

    result = await anyio.run_process(
        [
            sys.executable,
            "scripts/create_user.py",
            "--email",
            _OPS_EMAIL,
            "--subject",
            _OPS_SUBJECT,
            "--password-stdin",
        ],
        cwd=_SERVICE_ROOT,
        input=f"{_PASSWORD}\n".encode(),
        check=False,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr.decode()

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT roles, access_status FROM users WHERE subject = %s",
                (_OPS_SUBJECT,),
            )
            row = await cur.fetchone()
    assert row == {"roles": ["admin"], "access_status": "rejected"}

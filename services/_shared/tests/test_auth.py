"""测试 JWT 验证逻辑 + ``get_current_user`` FastAPI dependency。"""
from __future__ import annotations

import time
from typing import Annotated, Any

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from inalpha_shared.auth import User, get_current_user, verify_jwt
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.errors import UnauthorizedError
from inalpha_shared.middleware import install_error_handler

# 32+ 字节，避免 PyJWT 的 InsecureKeyLengthWarning
SECRET = "test-secret-do-not-use-in-prod-please-and-thank-you"


def make_token(payload: dict[str, Any], exp_offset: int = 3600) -> str:
    """生成测试用 JWT。"""
    full = {"sub": "user-123", "exp": int(time.time()) + exp_offset, **payload}
    return jwt.encode(full, SECRET, algorithm="HS256")


# ---------- 单元测试：verify_jwt ----------


def test_verify_jwt_valid() -> None:
    token = make_token({"sub": "abc", "email": "a@b.c"})
    payload = verify_jwt(token, SECRET)
    assert payload["sub"] == "abc"
    assert payload["email"] == "a@b.c"


def test_verify_jwt_expired() -> None:
    token = make_token({}, exp_offset=-120)
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_jwt(token, SECRET)
    assert exc_info.value.detail["code"] == "TOKEN_EXPIRED"


def test_verify_jwt_wrong_secret() -> None:
    token = make_token({})
    with pytest.raises(UnauthorizedError):
        verify_jwt(token, "different-secret-also-32-bytes-or-more")


def test_verify_jwt_malformed() -> None:
    with pytest.raises(UnauthorizedError):
        verify_jwt("not-a-jwt", SECRET)


# ---------- 集成测试：get_current_user dependency ----------


@pytest.fixture
def app_with_auth() -> FastAPI:
    """最小化的 FastAPI app，用 get_current_user dependency。"""
    app = FastAPI()
    install_error_handler(app)

    def override_settings() -> Settings:
        return Settings(  # type: ignore[call-arg]
            DATABASE_URL="postgresql://placeholder",
            JWT_SECRET=SECRET,
        )

    app.dependency_overrides[get_settings] = override_settings

    @app.get("/me")
    async def me(user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        return {"user_id": user.user_id, "email": user.email, "roles": user.roles}

    return app


def test_get_current_user_valid(app_with_auth: FastAPI) -> None:
    client = TestClient(app_with_auth)
    token = make_token({"email": "a@b.c", "roles": ["admin"]})
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "user-123", "email": "a@b.c", "roles": ["admin"]}


def test_get_current_user_missing_header(app_with_auth: FastAPI) -> None:
    client = TestClient(app_with_auth)
    r = client.get("/me")
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_get_current_user_wrong_scheme(app_with_auth: FastAPI) -> None:
    client = TestClient(app_with_auth)
    r = client.get("/me", headers={"Authorization": "Basic xyz"})
    assert r.status_code == 401


def test_get_current_user_expired_token(app_with_auth: FastAPI) -> None:
    client = TestClient(app_with_auth)
    token = make_token({}, exp_offset=-120)
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "TOKEN_EXPIRED"


def test_get_current_user_no_sub(app_with_auth: FastAPI) -> None:
    client = TestClient(app_with_auth)
    # 手动构造一个没有 sub 的 token
    token = jwt.encode({"exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_TOKEN_CLAIMS"


# ---------- review B1：算法白名单 + leeway ----------


def test_verify_jwt_rejects_none_algorithm() -> None:
    """alg=none downgrade attack 必须被拒（review B1）。"""
    token = make_token({})
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_jwt(token, SECRET, algorithm="none")
    assert exc_info.value.code == "INVALID_ALGORITHM"


def test_verify_jwt_rejects_rs256_when_secret_is_hmac() -> None:
    """alg=RS256 攻击（公开 RSA pubkey 当 HMAC key 签）必须被拒。"""
    token = make_token({})
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_jwt(token, SECRET, algorithm="RS256")
    assert exc_info.value.code == "INVALID_ALGORITHM"


def test_verify_jwt_leeway_accepts_recently_expired() -> None:
    """30s leeway 容忍跨机微小时钟漂移（review B1）。"""
    # 过期 5 秒前 —— 在 leeway 范围内
    token = make_token({}, exp_offset=-5)
    payload = verify_jwt(token, SECRET)
    assert payload["sub"] == "user-123"


def test_verify_jwt_long_expired_still_rejected() -> None:
    """leeway 不应让真正过期的 token 通过 —— 30s 之外必须拒。"""
    token = make_token({}, exp_offset=-120)
    with pytest.raises(UnauthorizedError) as exc_info:
        verify_jwt(token, SECRET)
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_verify_jwt_code_via_self_attr() -> None:
    """配合 errors.py 修复：UnauthorizedError 子类的 code 走 self（review #1）。"""
    token = make_token({}, exp_offset=-120)
    try:
        verify_jwt(token, SECRET)
    except UnauthorizedError as e:
        assert e.code == "TOKEN_EXPIRED"
    else:
        raise AssertionError("should have raised")

"""JWT 验证 + FastAPI ``get_current_user`` dependency。

MVP 简化：HS256 + 共享密钥。Next.js（better-auth / NextAuth）发 token，
所有 Python service 用同一个 ``JWT_SECRET`` 验签。

Phase F+ 评估 RS256 / JWKS（参考 [ADR-0002](../../../docs/decisions/0002-cross-service-communication.md)）。
"""
from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, Header
from pydantic import BaseModel

from .config import Settings, get_settings
from .errors import UnauthorizedError


class User(BaseModel):
    """JWT 解码后的用户视图。"""

    user_id: str
    email: str | None = None
    roles: list[str] = []


def verify_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    """验签 + 检查过期 + 返回 payload。

    抛 ``UnauthorizedError``（子类 ``TOKEN_EXPIRED``）。
    所有调用方 raise 后由 ``install_error_handler`` 统一处理。
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("token expired", code="TOKEN_EXPIRED") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedError(f"invalid token: {e}") from e
    return payload


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """FastAPI dependency: 从 ``Authorization`` 头取 JWT 解码出 ``User``。

    用法::

        from typing import Annotated
        from fastapi import Depends
        from quant_lab_shared.auth import User, get_current_user

        @app.get("/positions")
        def list_positions(user: Annotated[User, Depends(get_current_user)]):
            return {"user": user.user_id}
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    payload = verify_jwt(token, settings.jwt_secret, settings.jwt_algorithm)

    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedError("missing sub claim", code="INVALID_TOKEN_CLAIMS")

    return User(
        user_id=str(sub),
        email=payload.get("email"),
        roles=payload.get("roles", []) or [],
    )

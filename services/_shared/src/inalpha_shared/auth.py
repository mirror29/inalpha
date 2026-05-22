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


# JWT 算法白名单（D-8b' review B1：alg=none / alg=RS256 downgrade 防护）。
# 配置层 jwt_algorithm 即使被恶改也不能跑出本集合；MVP 只用 HMAC 对称密钥。
_ALLOWED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})

# 时钟漂移容忍秒数（D-8b' review B1：跨机微小漂移让 TOKEN_EXPIRED 误报）
_JWT_LEEWAY_SECONDS = 30


def verify_jwt(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    """验签 + 检查过期 + 返回 payload。

    安全约束（D-8b' review B1 修后强制）：

    - 算法白名单：仅 ``HS256/384/512``，``none`` / RS256 downgrade attack 不通过
    - 即使 ``settings.jwt_algorithm`` 被误配也不能突破白名单
    - 30s leeway 容忍跨机时钟漂移

    抛 ``UnauthorizedError``（子类 ``TOKEN_EXPIRED`` / ``INVALID_ALGORITHM``）。
    """
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise UnauthorizedError(
            f"jwt algorithm {algorithm!r} not in allowed set {sorted(_ALLOWED_ALGORITHMS)}",
            code="INVALID_ALGORITHM",
        )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            leeway=_JWT_LEEWAY_SECONDS,
        )
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
        from inalpha_shared.auth import User, get_current_user

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

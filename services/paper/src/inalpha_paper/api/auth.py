"""登录端点—— 校验 ``users`` 表里的账号密码,无鉴权。

链路:dashboard BFF ``POST /api/auth/login`` → (内网) 本端点 → argon2 verify →
返回 ``{subject, email, roles}``。dashboard 据此用 ``JWT_SECRET`` 签 httpOnly session
cookie(见 ``apps/dashboard/src/lib/session.ts``)。本端点**只校验密码,不签发 JWT**。

设计要点:

- **无 ``get_current_user`` 依赖**(登录本身就是拿凭据换身份,仿 ``api/health.py`` 无鉴权范式)。
- **argon2 verify 放线程池**(``anyio.to_thread.run_sync``):argon2 是 CPU 密集的同步调用,
  paper 是单进程且内嵌 live runner 事件循环,直接跑会卡住撮合循环。
- **抗用户枚举**:用户不存在时也对一个 dummy hash 跑一次 verify,再统一抛 401
  (``UNAUTHORIZED``),不区分"用户不存在 / 密码错",时序不泄露账号是否存在。
"""
from __future__ import annotations

from typing import Any, cast

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter
from inalpha_shared.db import DBConn
from inalpha_shared.errors import UnauthorizedError
from pydantic import BaseModel, Field

router = APIRouter(tags=["auth"])

_hasher = PasswordHasher()

# 用户不存在时拿来"陪跑"一次 verify 的占位哈希(抗时序型用户枚举)。值本身无意义
# ——任何真实密码都不会匹配它,只为消耗与真实 verify 相当的 CPU 时间。
_DUMMY_HASH = _hasher.hash("inalpha-dummy-password-for-timing-safety")


class LoginRequest(BaseModel):
    """``POST /auth/login`` 请求体。"""

    email: str = Field(description="登录邮箱(大小写不敏感)")
    password: str = Field(description="明文密码,仅用于本次校验,不落库不记日志")


class LoginResponse(BaseModel):
    """登录成功返回的用户身份(不含任何凭据)。"""

    subject: str = Field(description="JWT sub;dashboard 据此签 session、后端据此隔离数据")
    email: str
    roles: list[str] = Field(default_factory=list)


def _verify_password(password_hash: str, password: str) -> bool:
    """同步 argon2 verify(在线程池里调)。不匹配返回 False,不抛。"""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: DBConn) -> LoginResponse:
    """校验邮箱 + 密码,成功返回用户身份;失败统一 401。"""
    async with db.cursor() as cur:
        await cur.execute(
            "SELECT subject, email, password_hash, roles FROM users "
            "WHERE lower(email) = lower(%s)",
            (body.email,),
        )
        # 连接池用 dict_row row_factory,fetchone 返回 dict(psycopg 默认 stub 标 tuple)。
        row = cast("dict[str, Any] | None", await cur.fetchone())

    password_hash = row["password_hash"] if row else _DUMMY_HASH
    ok = await anyio.to_thread.run_sync(_verify_password, password_hash, body.password)
    if not row or not ok:
        raise UnauthorizedError("邮箱或密码不正确", code="INVALID_CREDENTIALS")

    return LoginResponse(
        subject=row["subject"],
        email=row["email"],
        roles=list(row["roles"] or []),
    )

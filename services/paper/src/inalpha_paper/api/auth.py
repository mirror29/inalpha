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
- **失败节流**:按邮箱做滑动窗口失败计数(paper 单进程,进程内 dict 即可),
  超阈值返 429,压住在线密码爆破。paper 只见 dashboard 容器同一来源 IP,故按邮箱
  维度而非 IP(per-IP 节流应在 Cloudflare / dashboard 边缘做)。
"""
from __future__ import annotations

import time
from typing import Any, cast

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter
from inalpha_shared.db import DBConn
from inalpha_shared.errors import RateLimitedError, UnauthorizedError
from pydantic import BaseModel, Field

router = APIRouter(tags=["auth"])

_hasher = PasswordHasher()

# 用户不存在时拿来"陪跑"一次 verify 的占位哈希(抗时序型用户枚举)。值本身无意义
# ——任何真实密码都不会匹配它,只为消耗与真实 verify 相当的 CPU 时间。
_DUMMY_HASH = _hasher.hash("inalpha-dummy-password-for-timing-safety")

# ── 按邮箱失败节流(进程内,paper 单进程/单副本)──
_LOGIN_WINDOW_S = 300.0  # 滑动窗口 5 分钟
_LOGIN_MAX_FAILS = 5  # 窗口内失败达此数 → 429
_LOGIN_TRACK_CAP = 10_000  # tracked 邮箱上界,超了整体清空(防内存膨胀,fail-open)
_login_failures: dict[str, list[float]] = {}


def _recent_failures(email_key: str, now: float) -> int:
    """返回窗口内失败次数,顺带剔除过期时间戳。"""
    recent = [t for t in _login_failures.get(email_key, []) if now - t < _LOGIN_WINDOW_S]
    if recent:
        _login_failures[email_key] = recent
    else:
        _login_failures.pop(email_key, None)
    return len(recent)


def _record_failure(email_key: str, now: float) -> None:
    if len(_login_failures) > _LOGIN_TRACK_CAP:
        _login_failures.clear()
    _login_failures.setdefault(email_key, []).append(now)


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
    """校验邮箱 + 密码,成功返回用户身份;失败统一 401,失败过频 429。"""
    email_key = body.email.strip().lower()
    now = time.monotonic()
    if _recent_failures(email_key, now) >= _LOGIN_MAX_FAILS:
        raise RateLimitedError("尝试过于频繁,请稍后再试", code="LOGIN_RATE_LIMITED")

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
        _record_failure(email_key, now)
        raise UnauthorizedError("邮箱或密码不正确", code="INVALID_CREDENTIALS")

    # 成功即清零该邮箱的失败计数(避免正常用户先错几次后被锁)。
    _login_failures.pop(email_key, None)
    return LoginResponse(
        subject=row["subject"],
        email=row["email"],
        roles=list(row["roles"] or []),
    )

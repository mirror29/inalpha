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
from collections import OrderedDict
from datetime import datetime
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn, get_conn
from inalpha_shared.errors import (
    ConflictError,
    ForbiddenError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from pydantic import BaseModel, Field, SecretStr
from structlog.contextvars import get_contextvars

router = APIRouter(tags=["auth"])

_hasher = PasswordHasher()
# Argon2 默认每次约占 64 MiB；登录与激活各用独立、非排队门控，饱和时立即 429。
# 不使用 CapacityLimiter 排队，避免攻击者堆积无限等待请求并长期饿死正常登录。
_login_hash_slots = anyio.Semaphore(2)
_activation_hash_slots = anyio.Semaphore(2)

# 用户不存在时拿来"陪跑"一次 verify 的占位哈希(抗时序型用户枚举)。值本身无意义
# ——任何真实密码都不会匹配它,只为消耗与真实 verify 相当的 CPU 时间。
_DUMMY_HASH = _hasher.hash(token_urlsafe(32))

# ── 按邮箱失败节流(进程内,paper 单进程/单副本)──
_LOGIN_WINDOW_S = 300.0  # 滑动窗口 5 分钟
_LOGIN_MAX_FAILS = 5  # 窗口内失败达此数 → 429
_LOGIN_TRACK_CAP = 10_000  # tracked 邮箱上界
# LRU(按最近活动排序):超上界时淘汰**最久未活动**的 key,而非整体清空。
# 后者会被"刷 1w+ 个不同邮箱各失败一次"直接清表、绕过对目标邮箱的节流;LRU 下
# 正在被爆破的目标每次失败都 move_to_end 保活,被淘汰的只会是早已沉底的旧 key。
_login_failures: OrderedDict[str, list[float]] = OrderedDict()

# 注册会写入公开数据库入口，除 Cloudflare 边缘限流外再做服务内双层保护。
_REGISTER_WINDOW_S = 3600.0
_REGISTER_MAX_PER_EMAIL = 3
_REGISTER_GLOBAL_WINDOW_S = 60.0
_REGISTER_GLOBAL_MAX = 20
_registration_attempts: OrderedDict[str, list[float]] = OrderedDict()
_registration_events: list[float] = []


def _recent_failures(email_key: str, now: float) -> int:
    """返回窗口内失败次数,顺带剔除过期时间戳并把该 key 记为最近活动。"""
    recent = [t for t in _login_failures.get(email_key, []) if now - t < _LOGIN_WINDOW_S]
    if recent:
        _login_failures[email_key] = recent
        _login_failures.move_to_end(email_key)
    else:
        _login_failures.pop(email_key, None)
    return len(recent)


def _record_failure(email_key: str, now: float) -> None:
    _login_failures.setdefault(email_key, []).append(now)
    _login_failures.move_to_end(email_key)
    while len(_login_failures) > _LOGIN_TRACK_CAP:
        _login_failures.popitem(last=False)  # 淘汰最久未活动的 key


def _discard_recorded_failure(email_key: str, timestamp: float) -> None:
    """撤销当前请求的乐观失败记录，同时保留该邮箱此前的真实失败。"""
    failures = _login_failures.get(email_key)
    if not failures:
        return
    try:
        failures.remove(timestamp)
    except ValueError:
        return
    if not failures:
        _login_failures.pop(email_key, None)


def _check_and_record_registration(email_key: str, now: float) -> None:
    """注册双层节流；检查与写入在无 await 的同步块中完成，避免并发穿透。"""
    global _registration_events
    _registration_events = [
        event for event in _registration_events if now - event < _REGISTER_GLOBAL_WINDOW_S
    ]
    recent = [
        event
        for event in _registration_attempts.get(email_key, [])
        if now - event < _REGISTER_WINDOW_S
    ]
    if len(_registration_events) >= _REGISTER_GLOBAL_MAX or len(recent) >= _REGISTER_MAX_PER_EMAIL:
        raise RateLimitedError("注册申请过于频繁,请稍后再试", code="REGISTER_RATE_LIMITED")
    recent.append(now)
    _registration_attempts[email_key] = recent
    _registration_attempts.move_to_end(email_key)
    _registration_events.append(now)
    while len(_registration_attempts) > _LOGIN_TRACK_CAP:
        _registration_attempts.popitem(last=False)


class LoginRequest(BaseModel):
    """``POST /auth/login`` 请求体。"""

    email: str = Field(description="登录邮箱(大小写不敏感)")
    password: SecretStr = Field(description="明文密码,仅用于本次校验,不落库不记日志")


class LoginResponse(BaseModel):
    """登录成功返回的用户身份(不含任何凭据)。"""

    subject: str = Field(description="JWT sub;dashboard 据此签 session、后端据此隔离数据")
    email: str
    roles: list[str] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    """公开注册申请；账号先进入 waitlist，不会立即获得访问权。"""

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)
    application_note: str = Field(default="", max_length=1000)


class RegisterResponse(BaseModel):
    """注册申请统一响应，避免根据返回值枚举已存在的邮箱。"""

    accepted: bool = True


class WaitlistUser(BaseModel):
    """管理员可见的待审申请；绝不包含 password_hash。"""

    subject: str
    email: str
    display_name: str | None
    application_note: str | None
    access_status: Literal["pending", "invited", "active", "rejected"]
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None


class WaitlistResponse(BaseModel):
    """待审申请列表。"""

    users: list[WaitlistUser]


class ReviewRequest(BaseModel):
    """管理员审核动作。"""

    decision: Literal["approve", "reject"]
    expected_reviewed_at: datetime | None = None


class ReviewResponse(BaseModel):
    """审核成功后的账号状态。"""

    subject: str
    access_status: Literal["invited", "rejected"]
    activation_token: str | None = None
    email: str | None = None


class ActivateRequest(BaseModel):
    """一次性激活链接提交的新密码。"""

    token: SecretStr = Field(min_length=32, max_length=512)
    password: SecretStr = Field(min_length=12, max_length=128)


class ActivateResponse(BaseModel):
    """激活成功响应。"""

    activated: bool = True


def _verify_password(password_hash: str, password: SecretStr) -> bool:
    """同步 argon2 verify(在线程池里调)。不匹配返回 False,不抛。"""
    try:
        return _hasher.verify(password_hash, password.get_secret_value())
    except VerifyMismatchError:
        return False


def _hash_password(password: SecretStr) -> str:
    """同步生成 Argon2 哈希；调用方必须放在线程池，避免阻塞事件循环。"""
    return _hasher.hash(password.get_secret_value())


def _normalize_email(email: str) -> str:
    """做最小但严格的邮箱规范化；完整可投递性留给后续邮件验证。"""
    normalized = email.strip().lower()
    local, separator, domain = normalized.partition("@")
    if (
        normalized.count("@") != 1
        or not separator
        or not local
        or not domain
        or "." not in domain
        or any(char.isspace() for char in normalized)
    ):
        raise ValidationError("邮箱格式不正确", code="INVALID_EMAIL")
    return normalized


async def _require_admin(user: User, db: DBConn) -> None:
    """从数据库实时校验 admin 角色，不信任客户端或旧 session 中的角色。"""
    async with db.cursor() as cur:
        await cur.execute("SELECT roles FROM users WHERE subject = %s", (user.user_id,))
        row = cast("dict[str, Any] | None", await cur.fetchone())
    if not row or "admin" not in list(row["roles"] or []):
        raise ForbiddenError("仅管理员可审核试用申请", code="ADMIN_REQUIRED")


def _current_trace_id() -> str | None:
    """返回当前请求 trace id，供访问状态事件与结构化日志关联。"""
    trace_id = get_contextvars().get("trace_id")
    return str(trace_id) if trace_id else None


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """校验邮箱 + 密码,成功返回用户身份;失败统一 401,失败过频 429。"""
    email_key = body.email.strip().lower()
    now = time.monotonic()
    # 检查 + 预记必须在**同一同步块内**(两者间无 await):asyncio 单线程下无 await
    # 即不切换协程,故此块对并发同邮箱请求是原子的——每个请求都先看到已递增的计数,
    # 堵住"同一批并发请求在写回失败前各自免费试一把密码"的并发爆破(check-then-act
    # 竞态)。verify 通过后再撤销这次预记。
    if _recent_failures(email_key, now) >= _LOGIN_MAX_FAILS:
        raise RateLimitedError("尝试过于频繁,请稍后再试", code="LOGIN_RATE_LIMITED")
    try:
        _login_hash_slots.acquire_nowait()
    except anyio.WouldBlock:
        raise RateLimitedError("登录服务繁忙,请稍后再试", code="LOGIN_BUSY") from None
    _record_failure(email_key, now)  # 拿到执行槽后才预记；繁忙请求不得锁定目标账号。
    try:
        async with get_conn() as db:
            async with db.cursor() as cur:
                await cur.execute(
                    # 用已 strip+lower 的 email_key,与节流 key 及建号时的存储保持一致
                    # (否则带首尾空格的邮箱查不到、却照样计入节流)。
                    "SELECT subject, email, password_hash, roles, access_status FROM users "
                    "WHERE lower(email) = %s",
                    (email_key,),
                )
                # 连接池用 dict_row row_factory,fetchone 返回 dict(psycopg 默认 stub 标 tuple)。
                row = cast("dict[str, Any] | None", await cur.fetchone())

        password_hash = row["password_hash"] if row else _DUMMY_HASH
        ok = await anyio.to_thread.run_sync(_verify_password, password_hash, body.password)
    except Exception:
        _discard_recorded_failure(email_key, now)
        raise
    finally:
        _login_hash_slots.release()
    if not row or not ok:
        # 失败:预记的那笔保留即计数,不重复记。
        raise UnauthorizedError("邮箱或密码不正确", code="INVALID_CREDENTIALS")

    # 密码正确即不算登录失败；待审/拒绝是授权状态，不应把用户累计进爆破节流。
    _login_failures.pop(email_key, None)
    if row["access_status"] != "active":
        raise ForbiddenError("账号当前不可登录", code="ACCOUNT_INACTIVE")

    return LoginResponse(
        subject=row["subject"],
        email=row["email"],
        roles=list(row["roles"] or []),
    )


@router.post("/auth/register", response_model=RegisterResponse, status_code=202)
async def register(body: RegisterRequest) -> RegisterResponse:
    """提交试用申请；重复邮箱统一返回 accepted，避免泄露账号是否存在。"""
    email = _normalize_email(body.email)
    display_name = body.display_name.strip()
    if not display_name:
        raise ValidationError("姓名不能为空", code="INVALID_DISPLAY_NAME")
    _check_and_record_registration(email, time.monotonic())
    async with get_conn() as db:
        async with db.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO users (
                    subject, email, password_hash, roles, access_status,
                    display_name, application_note
                )
                VALUES (%s, %s, %s, '{}', 'pending', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    f"user:{uuid4()}",
                    email,
                    _DUMMY_HASH,
                    display_name,
                    body.application_note.strip() or None,
                ),
            )
            await db.commit()
    return RegisterResponse()


@router.post("/auth/activate", response_model=ActivateResponse)
async def activate(body: ActivateRequest) -> ActivateResponse:
    """用管理员通过邮件发送的一次性令牌设置密码并激活账号。"""
    token_hash = sha256(body.token.get_secret_value().strip().encode("utf-8")).hexdigest()
    try:
        _activation_hash_slots.acquire_nowait()
    except anyio.WouldBlock:
        raise RateLimitedError("激活服务繁忙,请稍后再试", code="ACTIVATION_BUSY") from None
    try:
        async with get_conn() as db:
            async with db.cursor() as cur:
                await cur.execute(
                    "SELECT subject FROM users WHERE activation_token_hash = %s "
                    "AND access_status = 'invited' AND activation_expires_at > now()",
                    (token_hash,),
                )
                invited = cast("dict[str, Any] | None", await cur.fetchone())
        if not invited:
            raise ValidationError("激活链接无效或已过期", code="INVALID_ACTIVATION_TOKEN")

        password_hash = await anyio.to_thread.run_sync(_hash_password, body.password)
        async with get_conn() as db:
            async with db.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, access_status = 'active',
                        activation_token_hash = NULL, activation_expires_at = NULL,
                        updated_at = now()
                    WHERE subject = %s AND activation_token_hash = %s
                      AND access_status = 'invited' AND activation_expires_at > now()
                    RETURNING subject
                    """,
                    (password_hash, invited["subject"], token_hash),
                )
                activated = await cur.fetchone()
                if not activated:
                    await db.rollback()
                    raise ConflictError("激活链接已被使用", code="ACTIVATION_ALREADY_USED")
                await cur.execute(
                    """
                    INSERT INTO user_access_events (
                        target_subject, actor_subject, action, previous_status,
                        next_status, token_fingerprint, trace_id
                    ) VALUES (%s, %s, 'activated', 'invited', 'active', %s, %s)
                    """,
                    (
                        invited["subject"],
                        invited["subject"],
                        token_hash[:16],
                        _current_trace_id(),
                    ),
                )
                await db.commit()
    finally:
        _activation_hash_slots.release()
    return ActivateResponse()


@router.get("/auth/waitlist", response_model=WaitlistResponse)
async def list_waitlist(
    user: Annotated[User, Depends(get_current_user)], db: DBConn
) -> WaitlistResponse:
    """列出待审申请，只有数据库中具备 admin 角色的账号可调用。"""
    await _require_admin(user, db)
    async with db.cursor() as cur:
        await cur.execute(
            """
            SELECT subject, email, display_name, application_note, access_status,
                   created_at, reviewed_at, reviewed_by
            FROM users
            WHERE access_status IN ('pending', 'invited')
            ORDER BY CASE access_status WHEN 'pending' THEN 0 ELSE 1 END, created_at ASC
            LIMIT 200
            """
        )
        rows = cast("list[dict[str, Any]]", await cur.fetchall())
    return WaitlistResponse(users=[WaitlistUser.model_validate(row) for row in rows])


@router.post("/auth/waitlist/{subject}/review", response_model=ReviewResponse)
async def review_waitlist_user(
    subject: str,
    body: ReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: DBConn,
) -> ReviewResponse:
    """批准或拒绝一条 pending 申请；状态转换使用条件更新保证并发安全。"""
    await _require_admin(user, db)
    next_status: Literal["invited", "rejected"] = (
        "invited" if body.decision == "approve" else "rejected"
    )
    activation_secret = SecretStr(token_urlsafe(32)) if body.decision == "approve" else None
    activation_token_hash = (
        sha256(activation_secret.get_secret_value().encode("utf-8")).hexdigest()
        if activation_secret is not None
        else None
    )
    async with db.cursor() as cur:
        await cur.execute(
            """
            WITH target AS (
                SELECT subject, access_status AS previous_status
                FROM users
                WHERE subject = %s AND access_status IN ('pending', 'invited')
                  AND (
                      (%s::timestamptz IS NULL AND reviewed_at IS NULL)
                      OR reviewed_at = %s::timestamptz
                  )
                FOR UPDATE
            )
            UPDATE users AS reviewed
            SET access_status = %s, reviewed_at = now(), reviewed_by = %s,
                activation_token_hash = %s,
                activation_expires_at = CASE
                    WHEN %s::text IS NULL THEN NULL ELSE now() + interval '48 hours'
                END,
                updated_at = now()
            FROM target
            WHERE reviewed.subject = target.subject
            RETURNING reviewed.subject, reviewed.email, target.previous_status
            """,
            (
                subject,
                body.expected_reviewed_at,
                body.expected_reviewed_at,
                next_status,
                user.user_id,
                activation_token_hash,
                activation_token_hash,
            ),
        )
        row = cast("dict[str, Any] | None", await cur.fetchone())
        if not row:
            await db.rollback()
            raise ConflictError("申请不存在或已被审核", code="WAITLIST_ALREADY_REVIEWED")
        action = (
            "rejected"
            if body.decision == "reject"
            else "activation_rotated"
            if row["previous_status"] == "invited"
            else "approved"
        )
        await cur.execute(
            """
            INSERT INTO user_access_events (
                target_subject, actor_subject, action, previous_status,
                next_status, token_fingerprint, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["subject"],
                user.user_id,
                action,
                row["previous_status"],
                next_status,
                activation_token_hash[:16] if activation_token_hash else None,
                _current_trace_id(),
            ),
        )
        await db.commit()
    return ReviewResponse(
        subject=row["subject"],
        email=row.get("email"),
        access_status=next_status,
        activation_token=(
            activation_secret.get_secret_value() if activation_secret is not None else None
        ),
    )

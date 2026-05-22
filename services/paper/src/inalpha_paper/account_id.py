"""``account_id`` 来源：JWT ``sub`` → UUID。

JWT 的 ``sub`` 字段允许任意字符串（``"service:smoke"`` / ``"user-123"`` / 真 UUID 都可能）。
本模块负责把它**稳定地**映射成 ``UUID``（不论原始字符串是什么）。

策略：
1. 如果 ``sub`` 本身就是合法 UUID，直接 parse
2. 否则用 ``uuid5(NAMESPACE, sub)`` 派生一个稳定 UUID

效果：同一个 ``sub`` 永远对应同一个 ``account_id``，跨 service / 跨重启一致。
"""
from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from inalpha_shared.auth import User

# 用一个独立的 namespace UUID，避免和其他 service 用 NAMESPACE_DNS 算的撞
# uuid5(NAMESPACE_DNS, "inalpha.account") = 固定值
_ACCOUNT_NAMESPACE = uuid5(NAMESPACE_DNS, "inalpha.account")


def account_id_from_user(user: User) -> UUID:
    """``user.user_id``（字符串）→ ``UUID``，稳定可重现。"""
    return account_id_from_sub(user.user_id)


def account_id_from_sub(sub: str) -> UUID:
    try:
        return UUID(sub)
    except (ValueError, AttributeError):
        return uuid5(_ACCOUNT_NAMESPACE, sub)

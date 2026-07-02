"""users —— 多用户登录的账号表(DB 落库,替代单用户 dev token)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-01

背景:线上 dashboard 此前是单用户 dev 模式(固定 sub=console:dev)。本迁移落
``users`` 表存账号 + argon2 密码哈希,登录端点在 paper 服务(``POST /auth/login``)校验。

**supersede** 0001_initial_schema 里"users / sessions 交给 Next.js better-auth 管,本
migration 不碰"的旧决定——身份改为后端自管。

字段:

- ``subject``:JWT ``sub`` 字面量,也是 paper ``account_id_from_sub`` 的派生源(**主键**)。
  作者种 ``console:dev`` 以继承现有模拟盘数据;新用户各自派生独立 subject。
- ``email`` / ``username``:登录标识(email 必填唯一;username 可空唯一,留作备用登录名)。
- ``password_hash``:argon2 编码串(含算法 / 盐 / 参数,自描述)。
- ``roles``:预留权限位,v1 不做门,默认空。
"""
from __future__ import annotations

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            subject       TEXT PRIMARY KEY,
            email         TEXT NOT NULL,
            username      TEXT,
            password_hash TEXT NOT NULL,
            roles         TEXT[] NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # email / username 大小写不敏感唯一(登录不该因大小写重复开号)。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key "
        "ON users (lower(email))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_key "
        "ON users (lower(username)) WHERE username IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")

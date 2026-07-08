"""pending_approvals —— 加 auth_sub 列（跨用户审批日志隔离，#91 补全）

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-08

背景：#91 在 ask-cache 层面通过 AUTH_SUB_KEY（requestContext 里注入的已认证 JWT sub）
实现了按用户 scope 的审批缓存隔离，但审批审计日志表（pending_approvals）的写入与查询
没有任何身份过滤 —— 任一用户 GET /permissions/history 都看到全量记录（跨账号串号）。

本 migration 新增 auth_sub 列 + 复合索引：

- auth_sub TEXT：JWT sub（账户主体），INSERT 时由 repo.ts 写入
- 老数据 = NULL（不回填：仅查询侧做 IS NULL OR auth_sub = $2 兼容老行）
- 索引 (auth_sub, created_at DESC)：按用户过滤 + 时间倒序
"""
from __future__ import annotations

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE pending_approvals ADD COLUMN auth_sub TEXT")
    op.execute(
        "CREATE INDEX idx_pending_approvals_auth_sub"
        " ON pending_approvals (auth_sub, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pending_approvals_auth_sub")
    op.execute("ALTER TABLE pending_approvals DROP COLUMN IF EXISTS auth_sub")
"""factor_candidates —— 加 owner_account_id 列（多租户数据隔离）

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-08

因子候选池（factor_candidates）此前无 owner 概念，所有用户看到全部候选。
本次迁移（对齐 strategy_candidates 模式）：

- owner_account_id UUID：候选提出者的 account_id（JWT sub）
- 老数据 = NULL（不回填）
- 复合索引 (owner_account_id, created_at DESC)：按用户过滤 + 时间倒序
"""
from __future__ import annotations

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE factor_candidates ADD COLUMN owner_account_id UUID")
    op.execute(
        "CREATE INDEX idx_factor_candidates_owner"
        " ON factor_candidates (owner_account_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_factor_candidates_owner")
    op.execute("ALTER TABLE factor_candidates DROP COLUMN IF EXISTS owner_account_id")
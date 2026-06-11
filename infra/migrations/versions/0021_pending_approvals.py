"""pending_approvals —— ask 审批审计历史落库（重启可见，非恢复执行）

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-11

此前 ask 审批只活在 mastra 进程内存 Map（``permissions/pending.ts``）：
决策/超时后即从 dashboard 消失，mastra 重启挂起项整池蒸发 —— 用户视角
"重启后审批日志没了"。本表是**审计面**而非闸门：

- 闸门仍是内存 Promise（fail-closed，30s 超时 deny 不变）
- 每条审批全生命周期落一行；落库失败只 log 不阻断审批流（fail-open）
- mastra 启动时把遗留 ``pending`` 行批量置 ``expired_restart``：
  等待审批的那次 tool 执行随进程死亡，**不可能恢复继续执行**，
  落终态只为可见、可回看

status 枚举：pending / allowed / denied / expired_timeout / expired_restart。
"""
from __future__ import annotations

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pending_approvals (
            request_id UUID PRIMARY KEY,
            tool_name TEXT NOT NULL,
            tool_input JSONB,
            session_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'allowed', 'denied',
                                  'expired_timeout', 'expired_restart')),
            via TEXT
                CHECK (via IS NULL OR via IN ('user', 'timeout', 'restart')),
            created_at TIMESTAMPTZ NOT NULL,
            deadline TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_pending_approvals_status ON pending_approvals (status)"
    )
    op.execute(
        "CREATE INDEX idx_pending_approvals_created_at"
        " ON pending_approvals (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_approvals")

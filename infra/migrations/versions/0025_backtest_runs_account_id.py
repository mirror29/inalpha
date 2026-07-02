"""account_id 补洞 —— backtest_runs 按用户隔离(多租户上线必修)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-02

背景(PR #129 合并后上线验证):test2 用户登录后发现策略实验室 + 活动回测日志能
看到其他用户的回测与候选——上一轮多用户登录部署时漏了这两个端点的 per-account 过滤。

strategy_candidates 表已有 ``owner_account_id`` 列,问题只在 API 层没透传过滤;
本迁移专注 **backtest_runs 缺 account_id 列**——不加列无法过滤,comment 自己也写了
"坑（单租户假设）：backtest_runs 表无 owner 列……开放多用户前必须补 owner 过滤"。

``account_id TEXT``(可空,向后兼容老行)。索引 ``(account_id, created_at DESC)``
覆盖「查本人最近 N 条回测」的 8s 轮询热路径。
"""
from __future__ import annotations

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_runs
            ADD COLUMN IF NOT EXISTS account_id TEXT
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS backtest_runs_account_created_idx "
        "ON backtest_runs (account_id, created_at DESC) "
        "WHERE account_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backtest_runs_account_created_idx")
    op.execute(
        "ALTER TABLE backtest_runs DROP COLUMN IF EXISTS account_id"
    )

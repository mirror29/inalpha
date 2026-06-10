"""backtest_runs (created_at DESC) 单列索引。

控制台「Agent 活动」流每 8s 轮询 ``GET /backtest_runs``(无 filter →
``list_recent`` 按 created_at DESC LIMIT N)。现有复合索引
``backtest_runs_strategy_created_idx (strategy_id, created_at DESC)`` 在不带
strategy_id 谓词时帮不上 ORDER BY,查询退化为全表扫 + 排序——run 积累后
轮询会越来越重。补单列倒序索引让该查询走纯索引扫描。

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS backtest_runs_created_idx "
        "ON backtest_runs (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backtest_runs_created_idx")

"""因子血缘三字段 —— candidate 记"生成时依据"，run 记"入场基准"+告警状态机（ADR-0047）

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-11

"衰减值→后期策略方向"闭环的第一段（D-12）：

- ``strategy_candidates.factor_snapshot`` —— author_strategy 经 ``factorContext``
  显式传入的生成时因子上下文（venue/symbol/timeframe/as_of + factors[{id, rank_ic,
  rank_ic_recent, direction, decay_state}]）。candidate 依赖了哪些因子从此可追溯。
- ``strategy_runs.factor_baseline`` —— start_strategy 起跑时 best-effort 拍的
  factor /snapshot 快照 = 入场基准；巡检对比的锚点。factor 服务不可用时为 NULL，
  巡检首轮自愈补拍。
- ``strategy_runs.factor_alerts`` —— 衰减告警状态机 ``{factor_id: {state, alerted_at}}``；
  同 runner×因子 stable→decaying 只告警一次，恢复 stable 重置。

全部 nullable / 默认空、零回填：旧 candidate / run 无血缘是事实，不伪造。
不加索引：三列只按行主键读写，无按因子反查需求（出现时再补 GIN）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_candidates ADD COLUMN factor_snapshot JSONB")
    op.execute("ALTER TABLE strategy_runs ADD COLUMN factor_baseline JSONB")
    op.execute(
        "ALTER TABLE strategy_runs ADD COLUMN factor_alerts JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_runs DROP COLUMN IF EXISTS factor_alerts")
    op.execute("ALTER TABLE strategy_runs DROP COLUMN IF EXISTS factor_baseline")
    op.execute("ALTER TABLE strategy_candidates DROP COLUMN IF EXISTS factor_snapshot")

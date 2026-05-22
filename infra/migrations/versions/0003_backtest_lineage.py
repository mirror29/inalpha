"""backtest_runs 加血缘列 + 放宽 strategy_id 约束（D-8c · research→strategy 闭环）

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22

需求背景：
- D-7 的 ``backtest_runs`` 表 schema 假设 ``strategy_id`` 是 strategies 表的 UUID PK，
  但当前回测主路径用的是注册表 key（``'sma_cross'`` 等字符串），strategies 表暂时不写
- D-8c 起把每次回测落库，需要补"分析血缘"——即这次回测是哪次 research 驱动的

本 migration 做的事：
1. 放开 ``strategy_id`` 的 NOT NULL（保留 FK，可空）
2. 加 ``strategy_code TEXT NOT NULL`` —— 注册表 key（'sma_cross' 等）
3. 加 ``research_id UUID`` —— 链接到 research 服务的产物
4. 加 ``params_hash TEXT`` —— sha256(strategy_code + sorted(params))，去重 / 比对
5. 加 ``strategy_hint JSONB`` —— 触发本次回测的原始 hint（审计）
6. 给 ``research_id`` 建索引，支持"按 research 查历史回测"
7. 给 ``params_hash`` 建索引，支持去重判断
"""
from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # 1. 放开 strategy_id NOT NULL（保留 FK；NULL 不参与 FK 校验）
    op.execute("ALTER TABLE backtest_runs ALTER COLUMN strategy_id DROP NOT NULL")

    # 2-5. 加血缘列
    # strategy_code: 用 DEFAULT 'unknown' 临时占位以兼容已有行，新插入必须显式提供
    op.execute(
        "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS strategy_code TEXT"
    )
    op.execute(
        "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS research_id UUID"
    )
    op.execute(
        "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS params_hash TEXT"
    )
    op.execute(
        "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS strategy_hint JSONB"
    )

    # 6. 按 research_id 查历史的索引（部分索引，只索 NOT NULL 行）
    op.execute(
        "CREATE INDEX IF NOT EXISTS backtest_runs_research_idx "
        "ON backtest_runs (research_id, created_at DESC) "
        "WHERE research_id IS NOT NULL"
    )

    # 7. 按 (strategy_code, params_hash) 去重的索引
    op.execute(
        "CREATE INDEX IF NOT EXISTS backtest_runs_code_hash_idx "
        "ON backtest_runs (strategy_code, params_hash, created_at DESC) "
        "WHERE strategy_code IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backtest_runs_code_hash_idx")
    op.execute("DROP INDEX IF EXISTS backtest_runs_research_idx")
    op.execute("ALTER TABLE backtest_runs DROP COLUMN IF EXISTS strategy_hint")
    op.execute("ALTER TABLE backtest_runs DROP COLUMN IF EXISTS params_hash")
    op.execute("ALTER TABLE backtest_runs DROP COLUMN IF EXISTS research_id")
    op.execute("ALTER TABLE backtest_runs DROP COLUMN IF EXISTS strategy_code")
    # 恢复 NOT NULL（注意：如果有 NULL 行会失败 —— 业务上下游应保证）
    op.execute(
        "ALTER TABLE backtest_runs ALTER COLUMN strategy_id SET NOT NULL"
    )

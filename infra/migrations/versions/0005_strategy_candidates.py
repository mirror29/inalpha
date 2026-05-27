"""strategy_candidates 表（D-9 · ADR-0020 E1 MVP · LLM 自创策略候选池）

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25

D-9 需求：让 orchestrator agent 把"自己写的 Strategy 子类源码"落库存为候选。
回测自由跑、metrics 自动回写；**正式 promote 为可下单策略需人工**（ADR-0020
§关键约定 3，``modelInvocable: false``）。

本 migration 加一张 ``strategy_candidates`` 表：

- ``code`` —— 完整 Python 源码（已过 ast_audit / contract_check）
- ``code_hash`` —— sha256(code) 前 16 位 hex，UNIQUE 去重（相同源码不重复落）
- ``status`` —— ``candidate`` / ``rejected`` / ``promoted``
- ``metrics`` / ``fitness`` —— 最近一次回测的 BacktestReport 摘要 + 多目标 fitness
- ``last_backtest_run_id`` —— 引向 backtest_runs.id（详情 / equity_curve 在那查）
- ``audit`` —— 创建时 ast_audit 的 findings（通过的也存，便于复盘）

不引外键到 backtest_runs：last_backtest_run_id 是软引用——backtest_runs 可能因
data 重拉而淘汰旧行，候选不应被级联删。
"""
from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_candidates (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code                  TEXT NOT NULL,
            code_hash             TEXT NOT NULL UNIQUE,
            description           TEXT NOT NULL DEFAULT '',
            author                TEXT NOT NULL DEFAULT 'llm'
                                  CHECK (author IN ('llm', 'user', 'system')),
            author_id             UUID,
            status                TEXT NOT NULL DEFAULT 'candidate'
                                  CHECK (status IN ('candidate', 'rejected', 'promoted')),
            metrics               JSONB,
            fitness               DOUBLE PRECISION,
            last_backtest_run_id  UUID,
            audit                 JSONB,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX strategy_candidates_fitness_idx "
        "ON strategy_candidates (fitness DESC NULLS LAST, created_at DESC) "
        "WHERE status = 'candidate'"
    )
    op.execute(
        "CREATE INDEX strategy_candidates_author_idx "
        "ON strategy_candidates (author_id, created_at DESC) "
        "WHERE author_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS strategy_candidates_author_idx")
    op.execute("DROP INDEX IF EXISTS strategy_candidates_fitness_idx")
    op.execute("DROP TABLE IF EXISTS strategy_candidates")

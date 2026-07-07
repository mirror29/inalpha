"""E2 策略演化引擎 —— strategy_evo_runs + strategy_evo_candidates 表。

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-07

E2 演化闭环：LLM 驱动策略代码变异 → 三道沙盒 → 回测评估 → 落演化候选表。
与 paper 服务的 strategy_candidates 表**独立命名**（strategy_evo_*），避免冲突。

两表语义：
- strategy_evo_runs：一次演化会话（budget 个候选 = 一代）
- strategy_evo_candidates：单个变异 + 评估后的候选（含源码 / diff / report / fitness）
"""
from __future__ import annotations

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_evo_runs (
            run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            seed_strategy_id  TEXT NOT NULL,
            budget            INT NOT NULL CHECK (budget > 0),
            config            JSONB NOT NULL,
            status            TEXT NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running','completed','failed','aborted')),
            llm_cost_usd      NUMERIC(10,4) NOT NULL DEFAULT 0,
            candidates_count  INT NOT NULL DEFAULT 0,
            rejected_ast      INT NOT NULL DEFAULT 0,
            rejected_contract INT NOT NULL DEFAULT 0,
            failed_eval       INT NOT NULL DEFAULT 0,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at       TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_evo_candidates (
            candidate_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id            UUID NOT NULL REFERENCES strategy_evo_runs(run_id),
            generation        INT NOT NULL,
            parent_id         UUID REFERENCES strategy_evo_candidates(candidate_id),
            source_code       TEXT NOT NULL,
            source_hash       TEXT NOT NULL,
            unified_diff      TEXT,
            mutation_hint     TEXT,
            llm_cost_usd      NUMERIC(10,4),
            cache_hit_tokens  INT,
            fitness           DOUBLE PRECISION,
            report            JSONB NOT NULL,
            overfitting_risk  TEXT NOT NULL DEFAULT 'high'
                              CHECK (overfitting_risk IN ('high','medium','low')),
            data_epoch        BIGINT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'evaluated'
                              CHECK (status IN ('evaluated','proposed','rejected','registered')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (run_id, source_hash)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_evo_candidates_run_fitness
            ON strategy_evo_candidates (run_id, fitness DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_evo_candidates_status
            ON strategy_evo_candidates (status)
            WHERE status IN ('proposed', 'registered')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_evo_candidates_status")
    op.execute("DROP INDEX IF EXISTS idx_evo_candidates_run_fitness")
    op.execute("DROP TABLE IF EXISTS strategy_evo_candidates")
    op.execute("DROP TABLE IF EXISTS strategy_evo_runs")
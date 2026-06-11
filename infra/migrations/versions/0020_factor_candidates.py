"""factor_candidates 表 —— 自定义因子候选池（D-12 · 因子发现 L1 · ADR-0019 简化执行）

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-11

L1 因子发现的持久层：agent 经 ``factor.propose`` 把通过评估的表达式候选落进来
（status='pending_review'），**人工**在 dashboard 审核 → ``registered``（即生产：
custom adapter 把 registered 表达式注入 catalog/timing/score，无单独生产表）或
``rejected``。

register 门的实现比 ADR-0019 原定 modelInvocable=false 更硬：review 端点**不挂任何
LLM tool**——agent 物理上没有把候选转正的调用路径。

设计对齐 strategy_candidates（migration 0005）：

- ``expression_hash`` sha256 前 16 hex UNIQUE 幂等（LLM 重复 propose 同表达式返老行）
- ``hypothesis`` 强制 ≥ 20 字符——经济学故事门（没有"为什么该有效"的因子不收）
- ``test_results`` JSONB 存评估快照（rank_ic / icir / max_corr / p / adjusted_p ...）
- ``batch_id`` + ``n_tested``——多重检验审计锚点：这个候选是从一批多少个里试出来的
  （BH 校正的 m），复盘时可还原选择效应背景

表归 factor 服务所有（候选是因子域资产）；factor 服务无 DB 连接时 candidates
路由 503，timing/score/catalog 照常（可无 DB 启动语义不变）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE factor_candidates (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            expression       TEXT NOT NULL,
            expression_hash  TEXT NOT NULL UNIQUE,
            name             TEXT,
            hypothesis       TEXT NOT NULL CHECK (length(hypothesis) >= 20),
            proposed_by      TEXT NOT NULL DEFAULT 'agent',
            venue            TEXT,
            symbol           TEXT,
            timeframe        TEXT,
            test_results     JSONB NOT NULL,
            batch_id         UUID,
            n_tested         INT NOT NULL DEFAULT 1,
            status           TEXT NOT NULL DEFAULT 'pending_review'
                             CHECK (status IN ('pending_review', 'rejected', 'registered')),
            reviewed_by      TEXT,
            reviewed_at      TIMESTAMPTZ,
            review_note      TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX factor_candidates_status_idx "
        "ON factor_candidates (created_at DESC) WHERE status = 'pending_review'"
    )
    op.execute(
        "CREATE INDEX factor_candidates_registered_idx "
        "ON factor_candidates (updated_at DESC) WHERE status = 'registered'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS factor_candidates_registered_idx")
    op.execute("DROP INDEX IF EXISTS factor_candidates_status_idx")
    op.execute("DROP TABLE IF EXISTS factor_candidates")

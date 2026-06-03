"""strategy_run_decisions 加 intent 列 —— 复盘日志补做多/做空语义（D-11 CR）

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-02

``side`` 只有 BUY/SELL，无法区分"平多 vs 开空"（都是 SELL）、"平空 vs 开多"（都是 BUY），
做空策略的复盘日志缺语义（CLAUDE.md §4 多空纪律）。runner 在路由下单时已按"下单前持仓
方向 + side"算出 ``intent``（open_long / open_short / close）并写进了 plan rationale，
这里把它也落进 decision 行，复盘时间线即可直接看出每笔是开多/开空/平仓。

可空：CHECK 允许 NULL（旧行 / 异常路径未算出 intent 时不挡）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_run_decisions
            ADD COLUMN intent TEXT
                CHECK (intent IN ('open_long', 'open_short', 'close'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_run_decisions DROP COLUMN IF EXISTS intent")

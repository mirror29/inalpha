"""account_cash_flows —— 账户外生资金事件流水(充值/提取/重置)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-02

背景:账户现金此前只能 lazy create 固定 1 万,无充值/重置入口。模拟盘改钱 = 改绩效
口径(收益率分母/榜单/审计链),直接 UPDATE 余额会让战绩不可信——资金变更一律走
流水行 + 同事务更新 ``cash_balances``,``balance_after`` 冗余存变更后桶值供对账。

只记**外生**资金事件(deposit/withdraw/reset);成交现金变动由 orders /
closed_trades 承载,不重复记账。
"""
from __future__ import annotations

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_cash_flows (
            id BIGSERIAL PRIMARY KEY,
            account_id UUID NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('deposit', 'withdraw', 'reset')),
            currency TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            balance_after NUMERIC NOT NULL,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_cash_flows_account_created
            ON account_cash_flows (account_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_cash_flows")

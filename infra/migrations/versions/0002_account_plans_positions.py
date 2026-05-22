"""account / trade_plans / positions + orders extension

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

D-8b 持久化：
- accounts —— 用户级虚拟账户（cash + initial_cash）
- trade_plans —— Plan/Exec 计划落库（替代 orchestration 进程内 PlanStore）
- positions —— 用户级持仓累计
- orders —— 扩列 account_id / venue / symbol / fee / notional / trade_plan_id

为什么需要：D-8a' 全是 in-memory，回答不了"我现在持仓多少 / 今天下过哪些单"。
本 migration 给模拟盘加持久层 + user-level 隔离（account_id = JWT sub）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ============ accounts ============
    op.execute(
        """
        CREATE TABLE accounts (
            account_id     UUID PRIMARY KEY,
            initial_cash   NUMERIC NOT NULL DEFAULT 10000,
            cash           NUMERIC NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # ============ trade_plans ============
    op.execute(
        """
        CREATE TABLE trade_plans (
            plan_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id           UUID NOT NULL,
            intent               TEXT NOT NULL
                CHECK (intent IN ('open_long', 'open_short', 'close', 'rebalance')),
            venue                TEXT NOT NULL,
            symbol               TEXT NOT NULL,
            order_params         JSONB NOT NULL,
            risk_params          JSONB NOT NULL DEFAULT '{}'::jsonb,
            rationale            TEXT NOT NULL,
            status               TEXT NOT NULL
                CHECK (status IN ('pending_approval','approved','rejected','executed','expired')),
            approval_token       TEXT,
            approved_by          TEXT,
            rejection_reason     TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_at          TIMESTAMPTZ,
            executed_at          TIMESTAMPTZ,
            expire_at            TIMESTAMPTZ NOT NULL,
            resulting_order_id   TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX trade_plans_account_created_idx "
        "ON trade_plans (account_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX trade_plans_pending_idx "
        "ON trade_plans (account_id, status) WHERE status = 'pending_approval'"
    )
    # approval_token 唯一（NULL 允许多个；UPDATE...RETURNING 时校验）
    op.execute(
        "CREATE UNIQUE INDEX trade_plans_approval_token_uidx "
        "ON trade_plans (approval_token) WHERE approval_token IS NOT NULL"
    )

    # ============ positions ============
    op.execute(
        """
        CREATE TABLE positions (
            account_id      UUID NOT NULL,
            venue           TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            quantity        NUMERIC NOT NULL,
            avg_open_price  NUMERIC NOT NULL,
            realized_pnl    NUMERIC NOT NULL DEFAULT 0,
            generation      INT NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (account_id, venue, symbol)
        )
        """
    )
    op.execute(
        "CREATE INDEX positions_account_idx ON positions (account_id) "
        "WHERE quantity <> 0"
    )

    # ============ orders 扩列 ============
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS account_id UUID")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS venue TEXT")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS symbol TEXT")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS fee NUMERIC")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS notional NUMERIC")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS trade_plan_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS orders_account_ts_idx "
        "ON orders (account_id, ts_event DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS orders_account_symbol_idx "
        "ON orders (account_id, symbol, ts_event DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS orders_account_symbol_idx")
    op.execute("DROP INDEX IF EXISTS orders_account_ts_idx")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS trade_plan_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS notional")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS fee")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS symbol")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS venue")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS account_id")

    op.execute("DROP TABLE IF EXISTS positions")
    op.execute("DROP TABLE IF EXISTS trade_plans")
    op.execute("DROP TABLE IF EXISTS accounts")

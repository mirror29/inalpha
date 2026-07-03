"""strategy_run_allocation —— live run 的 per-run 资金额度(加列,向后兼容)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-02

背景:live run 的仓位测算此前用**固定 1 万虚拟现金**,脱离账户真实余额——多个 run
共享同一账户现金池却各自以为有 1 万,叠加"spot BUY 无购买力检查"后现金桶被买成
深度负值(USD 1 万开户、USDT 桶 -9,498 实锤)。

本迁移给 ``strategy_runs`` 加 ``allocation``(该 run 的资金额度,start 时确定并落库,
使 sizing 行为可复现、可审计):

- nullable:老行为空 = 沿用旧语义(固定 1 万),新 run 由 API 层写入
  ``min(10000, 账户折算可用现金)`` 或用户显式值。
- 计价按账户 ``base_currency``(与 accounts.cash_balances 折算口径一致)。
"""
from __future__ import annotations

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_runs
            ADD COLUMN IF NOT EXISTS allocation NUMERIC
                CHECK (allocation IS NULL OR allocation > 0)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_runs DROP COLUMN IF EXISTS allocation")

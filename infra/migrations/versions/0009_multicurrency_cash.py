"""accounts 多币种 cash（cash_balances + base_currency）+ positions.currency

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-01

D-11 跨币种 cash model：一个模拟盘账户可同时持有不同市场的标的（BTC/USDT、AAPL、
sh.600519），各自计价货币不同。原 ``accounts.cash`` 单标量无法表达多币种现金，
``equity()`` 直接把不同币种的现金 / 持仓相加会算错。

本 migration：

- ``positions.currency``：每个持仓的计价货币（NULL = 0008 之前的旧行，读取层按
  ``(venue, symbol)`` 用 ``currency_resolver`` 再解析兜底）
- ``accounts.base_currency``：账户报告 / equity 折算的目标货币（默认 USD）
- ``accounts.cash``（单 NUMERIC）→ ``accounts.cash_balances``（JSONB，按币种分桶）：
  金额以 **string** 存（``{"USD": "10000.00"}``），避免 JSONB number 走 IEEE754 浮点漂移；
  Python 侧读出后 Decimal 化。现有 cash 归入 base_currency 桶（旧语义是单一 quote）。

读取方收敛在 ``storage/accounts.py`` / ``api/orders.py`` / ``api/trade_plans.py``
三处（均随本里程碑改造），故安全地 DROP 旧 ``cash`` 列。
"""
from __future__ import annotations

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ── positions：计价货币列 ──
    op.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS currency TEXT")

    # ── accounts：base currency + 多币种 cash 桶 ──
    op.execute(
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
        "base_currency TEXT NOT NULL DEFAULT 'USD'"
    )
    op.execute(
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
        "cash_balances JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    # 现有 cash → base_currency 桶（金额 string 存，避免 JSONB float 漂移）
    op.execute(
        """
        UPDATE accounts
        SET cash_balances = jsonb_build_object(base_currency, (cash)::text)
        WHERE cash IS NOT NULL AND cash_balances = '{}'::jsonb
        """
    )
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS cash")


def downgrade() -> None:
    # 还原单标量 cash（取 base_currency 桶，best-effort）
    op.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS cash NUMERIC")
    op.execute(
        """
        UPDATE accounts
        SET cash = COALESCE((cash_balances ->> base_currency)::numeric, 0)
        """
    )
    op.execute("ALTER TABLE accounts ALTER COLUMN cash SET NOT NULL")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS cash_balances")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS base_currency")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS currency")

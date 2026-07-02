"""accounts 表的读写 —— 用户级虚拟账户（D-11 多币种 cash）。

D-8b 起：每个用户（JWT sub）一份独立账户，首次下单时 lazy create。
D-11 起：现金从单标量 ``cash`` 改为 ``cash_balances``（JSONB，按币种分桶），
账户带 ``base_currency``（报告 / equity 折算目标，默认 USD）。

一个账户可同时持有不同市场标的（BTC/USDT、AAPL、sh.600519），每个 cash 桶按
**计价货币**（instrument 的 quote currency，见 ``execution.currency_resolver``）记账：
crypto BUY 扣 USDT 桶、美股 BUY 扣 USD 桶、A股 BUY 扣 CNY 桶。桶可为负（模拟盘允许
"借"余额，与 D-8b 既有行为一致），总权益折算时按 FX 汇总到 base_currency。

金额在 JSONB 里以 **string** 存（``{"USD": "10000.00"}``），避免 JSONB number 走
IEEE754 浮点漂移；读出后 Decimal 化。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

DEFAULT_INITIAL_CASH = Decimal("10000")
DEFAULT_BASE_CURRENCY = "USD"


async def get_or_create(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    initial_cash: Decimal = DEFAULT_INITIAL_CASH,
    base_currency: str = DEFAULT_BASE_CURRENCY,
    for_update: bool = False,
) -> dict[str, Any]:
    """按 account_id 查账户；不存在则按默认初始资金创建。

    初始资金落在 ``base_currency`` 桶（``cash_balances = {base_currency: initial_cash}``）。
    幂等：UPSERT 走 ON CONFLICT DO NOTHING，并发首单不会重复初始化。
    返回最新账户行（含 ``initial_cash`` / ``base_currency`` / ``cash_balances`` dict）。

    ``for_update=True``:``SELECT ... FOR UPDATE`` 锁账户行——spot BUY 购买力守门在
    事务内复检时用,把"读余额 → 校验 → 扣款"串行化,堵并发 BUY 各读旧余额双双过闸
    的 TOCTOU(与 positions 行 SELL 守门同构)。须在事务内调用。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO accounts (account_id, initial_cash, base_currency, cash_balances)
            VALUES (%s::uuid, %s::numeric, %s::text,
                    jsonb_build_object(%s::text, (%s::numeric)::text))
            ON CONFLICT (account_id) DO NOTHING
            """,
            (str(account_id), initial_cash, base_currency, base_currency, initial_cash),
        )
        await cur.execute(
            "SELECT account_id, initial_cash, base_currency, cash_balances, "
            "created_at, updated_at "
            "FROM accounts WHERE account_id = %s"
            + (" FOR UPDATE" if for_update else ""),
            (str(account_id),),
        )
        row = await cur.fetchone()
    if row is None:  # 理论上不会
        raise RuntimeError(f"account {account_id} not found after upsert")
    return row  # type: ignore[return-value]


async def get(conn: AsyncConnection, account_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT account_id, initial_cash, base_currency, cash_balances, "
            "created_at, updated_at "
            "FROM accounts WHERE account_id = %s",
            (str(account_id),),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def apply_cash_delta(
    conn: AsyncConnection,
    account_id: UUID,
    delta: Decimal,
    *,
    currency: str,
) -> Decimal:
    """原子更新某币种桶 ``cash_balances[currency] += delta``，返回该桶新值。

    delta < 0 = 买单扣款；delta > 0 = 卖单入账。不做余额检查（D-8b 模拟盘允许"借"
    余额）。桶不存在时自动创建（``jsonb_set ... create_missing=true``）。金额在 JSONB
    里以 string 存，``::numeric`` / ``::text`` 往返保 Decimal 精度。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE accounts
            SET cash_balances = jsonb_set(
                    cash_balances,
                    ARRAY[%s::text],
                    to_jsonb(
                        (COALESCE(cash_balances ->> %s::text, '0')::numeric + %s::numeric)::text
                    ),
                    true
                ),
                updated_at = NOW()
            WHERE account_id = %s::uuid
            RETURNING (cash_balances ->> %s::text)::numeric AS new_amount
            """,
            (currency, currency, delta, str(account_id), currency),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"account {account_id} not found when applying cash delta")
    return Decimal(row["new_amount"])  # type: ignore[index]

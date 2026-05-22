"""trade_plans 表读写 + 状态机（D-8b 起，替代 orchestration 进程内 PlanStore）。

状态机：``pending_approval → approved → executed`` (happy path)
        ``pending_approval → rejected`` (拒绝)
        ``pending_approval → expired`` (过期自动)
        ``approved → expired`` (过期自动，token 未消费即过期)

并发安全：
- approval_token 一次性消费用 ``UPDATE ... WHERE approval_token = X AND status = 'approved'
  RETURNING`` 原子操作（防重放 / 并发 execute）
- 过期判定走 lazy check：每次读 plan 时若 expire_at < NOW() 且状态非终态，
  自动写回 status='expired'
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

DEFAULT_EXPIRE_SECONDS = 300


class PlanError(Exception):
    """业务错误（不是 DB 错误）。code 给 HTTP 层翻译成响应。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ────────────────────────────────────────────────────────────────────
# 写
# ────────────────────────────────────────────────────────────────────


async def create(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    intent: str,
    venue: str,
    symbol: str,
    order_params: dict[str, Any],
    rationale: str,
    expire_in_seconds: int = DEFAULT_EXPIRE_SECONDS,
    risk_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建 plan，状态 = pending_approval。返回完整行。"""
    if not rationale.strip():
        raise PlanError(
            "RATIONALE_REQUIRED",
            "rationale must be non-empty (审计要求 LLM 解释决策动机)",
        )
    quantity = order_params.get("quantity", 0)
    if not isinstance(quantity, (int, float)) or quantity <= 0:
        raise PlanError("INVALID_QUANTITY", f"quantity must be > 0, got {quantity}")

    order_type = order_params.get("type")
    price = order_params.get("price")
    if order_type == "LIMIT" and price is None:
        raise PlanError("LIMIT_REQUIRES_PRICE", "LIMIT orderParams must specify price")
    if order_type == "MARKET" and price is not None:
        raise PlanError("MARKET_NO_PRICE", "MARKET orderParams must not specify price")

    now = datetime.now(UTC)
    expire_at = now + timedelta(seconds=expire_in_seconds)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO trade_plans (
                account_id, intent, venue, symbol, order_params, risk_params,
                rationale, status, expire_at
            ) VALUES (
                %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s, 'pending_approval', %s
            )
            RETURNING plan_id, account_id, intent, venue, symbol, order_params,
                      risk_params, rationale, status, approval_token, approved_by,
                      rejection_reason, created_at, approved_at, executed_at,
                      expire_at, resulting_order_id
            """,
            (
                str(account_id),
                intent,
                venue,
                symbol,
                json.dumps(order_params),
                json.dumps(risk_params or {}),
                rationale,
                expire_at,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("trade_plan insert returned no row")
    return row  # type: ignore[return-value]


async def approve(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    plan_id: UUID,
    approver: str,
) -> dict[str, Any]:
    """审批 plan，发放一次性 approval_token。

    用 ``UPDATE ... WHERE status = 'pending_approval' AND expire_at > NOW() RETURNING``
    原子操作，避免 race condition。
    """
    token = secrets.token_urlsafe(32)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE trade_plans
            SET status = 'approved',
                approval_token = %s,
                approved_by = %s,
                approved_at = NOW()
            WHERE plan_id = %s
              AND account_id = %s
              AND status = 'pending_approval'
              AND expire_at > NOW()
            RETURNING plan_id, status, approval_token, approved_at
            """,
            (token, approver, str(plan_id), str(account_id)),
        )
        row = await cur.fetchone()

    if row is None:
        # 查清楚为啥失败：plan 不存在 / 状态不对 / 过期
        await _raise_for_invalid_state(conn, account_id, plan_id, expected="pending_approval")
    return row  # type: ignore[return-value]


async def reject(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    plan_id: UUID,
    reason: str,
    rejector: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise PlanError("REASON_REQUIRED", "rejection reason must be non-empty")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE trade_plans
            SET status = 'rejected',
                rejection_reason = %s,
                approved_by = %s
            WHERE plan_id = %s
              AND account_id = %s
              AND status = 'pending_approval'
            RETURNING plan_id, status, rejection_reason
            """,
            (reason, rejector, str(plan_id), str(account_id)),
        )
        row = await cur.fetchone()
    if row is None:
        await _raise_for_invalid_state(conn, account_id, plan_id, expected="pending_approval")
    return row  # type: ignore[return-value]


async def consume_approval(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    plan_id: UUID,
    approval_token: str,
) -> dict[str, Any]:
    """原子消费 approval_token —— UPDATE...RETURNING + token 置 NULL。

    返回完整 plan 行（含 order_params 给 caller 拿去撮合）。
    调用方拿到结果后**必须**调 :func:`record_execution` 完成状态切换。

    在事务里调；如果后续 OrderExecutor 失败，回滚事务则 token 也回滚（保持 'approved' 可重试）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE trade_plans
            SET approval_token = NULL
            WHERE plan_id = %s
              AND account_id = %s
              AND status = 'approved'
              AND approval_token = %s
              AND expire_at > NOW()
            RETURNING plan_id, account_id, intent, venue, symbol, order_params,
                      risk_params, rationale, status, approved_by, created_at,
                      approved_at, expire_at
            """,
            (str(plan_id), str(account_id), approval_token),
        )
        row = await cur.fetchone()
    if row is None:
        await _raise_for_invalid_state(conn, account_id, plan_id, expected="approved")
    return row  # type: ignore[return-value]


async def record_execution(
    conn: AsyncConnection,
    *,
    plan_id: UUID,
    resulting_order_id: str,
) -> dict[str, Any]:
    """consume_approval 后用，把 plan 切到 executed + 写回订单 ID。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE trade_plans
            SET status = 'executed',
                executed_at = NOW(),
                resulting_order_id = %s
            WHERE plan_id = %s
            RETURNING plan_id, status, executed_at, resulting_order_id
            """,
            (resulting_order_id, str(plan_id)),
        )
        row = await cur.fetchone()
    if row is None:
        raise PlanError("PLAN_NOT_FOUND", f"plan {plan_id} not found when recording execution")
    return row  # type: ignore[return-value]


# ────────────────────────────────────────────────────────────────────
# 读
# ────────────────────────────────────────────────────────────────────


async def get(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    plan_id: UUID,
) -> dict[str, Any] | None:
    """查单个 plan（按 account 隔离）。读时自动把过期未终态的标记为 expired。"""
    async with conn.cursor() as cur:
        # lazy expire
        await cur.execute(
            """
            UPDATE trade_plans
            SET status = 'expired'
            WHERE plan_id = %s
              AND account_id = %s
              AND status IN ('pending_approval', 'approved')
              AND expire_at <= NOW()
            """,
            (str(plan_id), str(account_id)),
        )
        await cur.execute(
            """
            SELECT plan_id, account_id, intent, venue, symbol, order_params,
                   risk_params, rationale, status, approved_by, rejection_reason,
                   created_at, approved_at, executed_at, expire_at, resulting_order_id
            FROM trade_plans
            WHERE plan_id = %s AND account_id = %s
            """,
            (str(plan_id), str(account_id)),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def list_by_account(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出某 account 的 plans，按 created_at DESC。"""
    sql = (
        "SELECT plan_id, intent, venue, symbol, order_params, status, "
        "rationale, approved_by, rejection_reason, created_at, approved_at, "
        "executed_at, expire_at, resulting_order_id "
        "FROM trade_plans WHERE account_id = %s"
    )
    params: list[Any] = [str(account_id)]
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────────
# 内部
# ────────────────────────────────────────────────────────────────────


async def _raise_for_invalid_state(
    conn: AsyncConnection,
    account_id: UUID,
    plan_id: UUID,
    *,
    expected: str,
) -> None:
    """UPDATE 0 行时调，查出具体原因并抛 PlanError。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT status, expire_at FROM trade_plans "
            "WHERE plan_id = %s AND account_id = %s",
            (str(plan_id), str(account_id)),
        )
        row = await cur.fetchone()

    if row is None:
        raise PlanError(
            "PLAN_NOT_FOUND",
            f"plan {plan_id} not found for account {account_id}",
            {"planId": str(plan_id)},
        )
    status = row["status"]  # type: ignore[index]
    expire_at = row["expire_at"]  # type: ignore[index]
    now = datetime.now(UTC)
    if expire_at <= now and status in ("pending_approval", "approved"):
        # 标记为 expired
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE trade_plans SET status = 'expired' WHERE plan_id = %s",
                (str(plan_id),),
            )
        raise PlanError(
            "PLAN_EXPIRED",
            f"plan {plan_id} expired at {expire_at.isoformat()}",
            {"planId": str(plan_id), "expireAt": expire_at.isoformat()},
        )
    raise PlanError(
        "INVALID_STATE",
        f"cannot transition plan in status '{status}' (expected '{expected}')",
        {"planId": str(plan_id), "status": status},
    )

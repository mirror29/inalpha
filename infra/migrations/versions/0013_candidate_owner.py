"""strategy_candidates 加 owner_account_id —— 跨用户归属校验（D-11.1 issue #36.1）

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-03

live runner 让 promoted candidate 按行情**无人值守自动下单**。原 ``POST /strategy_runs``
只校验 candidate ``status='promoted'``，**不校验调用者是否拥有该 candidate**——任何用户
能拿别人 promote 的 ``candidate_id`` 挂在**自己账户**下跑。

修法不能直接拿现有 ``author_id`` 比 run 的 ``account_id``：

- ``author_id`` 走 ``_try_uuid(user.user_id)``：非 UUID ``sub`` → ``NULL``
- ``account_id`` 走 ``account_id_from_user``：非 UUID ``sub`` → ``uuid5`` 兜底

两者对非 UUID ``sub`` 用户**不一致**。本 migration 引入与 run 归属**同源**的
``owner_account_id``（创建时由 ``account_id_from_user(user)`` 写入），统一归属语义。

回填：``owner_account_id = author_id WHERE author_id IS NOT NULL``——UUID ``sub`` 场景
``author_id`` 恒等于 ``account_id``，回填正确。``author_id IS NULL`` 的遗留行（非 UUID
``sub`` 老数据）保持 ``NULL``；start 路径对 ``NULL`` 行放行（无法追溯归属，仅限
pre-migration 行的有界 fail-open，见 ``api/strategy_runs.py``）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_candidates ADD COLUMN owner_account_id UUID")
    # 回填：author_id 非空（UUID sub 场景，author_id == account_id）→ 作为 owner
    op.execute(
        "UPDATE strategy_candidates SET owner_account_id = author_id "
        "WHERE author_id IS NOT NULL"
    )
    # 按 owner 过滤 run 起跑校验
    op.execute(
        "CREATE INDEX strategy_candidates_owner_idx "
        "ON strategy_candidates (owner_account_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS strategy_candidates_owner_idx")
    op.execute("ALTER TABLE strategy_candidates DROP COLUMN IF EXISTS owner_account_id")

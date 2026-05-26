"""risk_locks 表（D-9 · ADR-0006 · 执行层风控锁持久化）

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-26

[ADR-0006](../../../docs/miro/decisions/0006-risk-rules.md) §Architecture
定义的 `risk_locks` 表。RiskEngine 命中 RiskRule 时写入；未来 Slice 加 API /
agent MCP tool 查询 active locks + 人工 unlock。

3 层 scope：
- ``global`` —— 全账户锁（market / symbol 字段为 NULL）
- ``market`` —— 单 market 锁（market 字段必填，symbol NULL；market = venue 字符串）
- ``symbol`` —— 单 symbol 锁（symbol 必填；market 也填上便于按 market 聚合查询）

side 字段支持单边锁（long/short 单一方向 vs 双向 '*'），见 ADR-0006 §3.1。

`active` 字段允许人工 unlock 时软删（保留审计 row），不真 DELETE。
"""
from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE risk_locks (
            id              BIGSERIAL PRIMARY KEY,
            scope           VARCHAR(16) NOT NULL
                            CHECK (scope IN ('global', 'market', 'symbol')),
            market          VARCHAR(64),
            symbol          VARCHAR(128),
            side            VARCHAR(8) NOT NULL DEFAULT '*'
                            CHECK (side IN ('long', 'short', '*')),
            rule_name       VARCHAR(64) NOT NULL,
            reason          TEXT NOT NULL,
            locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            locked_until    TIMESTAMPTZ NOT NULL,
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            unlocked_at     TIMESTAMPTZ,
            unlocked_by     TEXT,
            unlock_reason   TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX risk_locks_active_idx "
        "ON risk_locks (active, scope, market, symbol, locked_until DESC) "
        "WHERE active = TRUE"
    )
    op.execute(
        "CREATE INDEX risk_locks_locked_at_idx "
        "ON risk_locks (locked_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS risk_locks_locked_at_idx")
    op.execute("DROP INDEX IF EXISTS risk_locks_active_idx")
    op.execute("DROP TABLE IF EXISTS risk_locks")

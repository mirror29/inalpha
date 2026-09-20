"""Allow bounded E2 campaign approvals in the durable operation ledger."""

from __future__ import annotations

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Permit both E1 runs and E2 campaigns in the shared approval-operation ledger.

    ``0042`` originally introduced the ledger, but some long-lived development
    databases were stamped past that revision before the table shipped.  Repair
    that rolling-upgrade state here so upgrading an existing installation cannot
    strand the whole application at startup.
    """
    op.execute(
        """SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS evolution_approval_operations (
  operation_id UUID PRIMARY KEY,
  auth_sub TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
  approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  UNIQUE(auth_sub,session_id,tool_name,input_digest),
  CHECK (expires_at>approved_at)
);
CREATE INDEX IF NOT EXISTS ix_evolution_approval_operations_expires_at
ON evolution_approval_operations(expires_at);
ALTER TABLE evolution_approval_operations
DROP CONSTRAINT IF EXISTS evolution_approval_operations_tool_name_check;
ALTER TABLE evolution_approval_operations
ADD CONSTRAINT evolution_approval_operations_tool_name_check
CHECK (tool_name IN ('evolver.run_evolution','evolver.run_event_campaign'));"""
    )


def downgrade() -> None:
    """Remove E2 recovery rows before restoring the E1-only ledger constraint."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DELETE FROM evolution_approval_operations
WHERE tool_name='evolver.run_event_campaign';
ALTER TABLE evolution_approval_operations
DROP CONSTRAINT IF EXISTS evolution_approval_operations_tool_name_check;
ALTER TABLE evolution_approval_operations
ADD CONSTRAINT evolution_approval_operations_tool_name_check
CHECK (tool_name='evolver.run_evolution');"""
    )

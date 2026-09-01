"""Allow bounded E2 campaign approvals in the durable operation ledger."""

from __future__ import annotations

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Permit both E1 runs and E2 campaigns in the shared approval-operation ledger."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_approval_operations
DROP CONSTRAINT evolution_approval_operations_tool_name_check;
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
DROP CONSTRAINT evolution_approval_operations_tool_name_check;
ALTER TABLE evolution_approval_operations
ADD CONSTRAINT evolution_approval_operations_tool_name_check
CHECK (tool_name='evolver.run_evolution');"""
    )

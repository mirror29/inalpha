"""Allow owner-approved durable loop commands in the existing encrypted approval ledger."""
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_approval_operations DROP CONSTRAINT evolution_approval_operations_tool_name_check;
ALTER TABLE evolution_approval_operations ADD CONSTRAINT evolution_approval_operations_tool_name_check
CHECK (tool_name IN ('evolver.run_evolution','evolver.run_event_campaign','evolver.start_evolution_loop'));
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM evolution_approval_operations WHERE tool_name='evolver.start_evolution_loop') THEN
  RAISE EXCEPTION 'cannot downgrade 0067 with recorded loop approvals';
 END IF;
END $$;
ALTER TABLE evolution_approval_operations DROP CONSTRAINT evolution_approval_operations_tool_name_check;
ALTER TABLE evolution_approval_operations ADD CONSTRAINT evolution_approval_operations_tool_name_check
CHECK (tool_name IN ('evolver.run_evolution','evolver.run_event_campaign'));
""")

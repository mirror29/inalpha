"""Persist non-secret frozen commands for owner-authenticated approval recovery."""
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_approval_operations ADD COLUMN execution_input JSONB;
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM evolution_approval_operations WHERE execution_input IS NOT NULL) THEN
  RAISE EXCEPTION 'cannot downgrade 0063 with recorded approved commands';
 END IF;
END $$;
ALTER TABLE evolution_approval_operations DROP COLUMN execution_input;
""")

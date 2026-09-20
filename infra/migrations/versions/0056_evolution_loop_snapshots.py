"""Freeze a loop's dataset before its baseline, without rewriting immutable rows."""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow exactly one owning loop or legacy campaign per snapshot."""
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_data_snapshots ALTER COLUMN campaign_id DROP NOT NULL;
ALTER TABLE evolution_data_snapshots ADD COLUMN loop_id UUID UNIQUE
  REFERENCES evolution_loops(loop_id);
ALTER TABLE evolution_data_snapshots ADD CONSTRAINT evolution_snapshot_single_anchor
  CHECK ((campaign_id IS NULL) <> (loop_id IS NULL));
CREATE INDEX ix_evolution_loops_e1_run ON evolution_loops(e1_run_id)
  WHERE e1_run_id IS NOT NULL;
""")


def downgrade() -> None:
    """Refuse to discard frozen loop research evidence."""
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM evolution_data_snapshots WHERE loop_id IS NOT NULL) THEN
    RAISE EXCEPTION 'cannot downgrade 0056 with frozen loop datasets';
  END IF;
END $$;
DROP INDEX ix_evolution_loops_e1_run;
ALTER TABLE evolution_data_snapshots DROP CONSTRAINT evolution_snapshot_single_anchor;
ALTER TABLE evolution_data_snapshots DROP COLUMN loop_id;
ALTER TABLE evolution_data_snapshots ALTER COLUMN campaign_id SET NOT NULL;
""")

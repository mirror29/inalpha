"""Fence pre-campaign EvolutionLoop workers and persist idempotent handoffs."""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep workflow ownership separate from E1 execution and campaign leases."""
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_loops
  ADD COLUMN lease_owner TEXT,
  ADD COLUMN lease_token UUID,
  ADD COLUMN lease_expires_at TIMESTAMPTZ,
  ADD COLUMN next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD CONSTRAINT ck_evolution_loop_lease_tuple CHECK (
    (lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR
    (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
  );
CREATE INDEX ix_evolution_loop_dispatch
ON evolution_loops(next_attempt_at,updated_at,loop_id)
WHERE campaign_id IS NULL AND status IN ('target_resolved','baseline_ready');
CREATE TABLE evolution_loop_steps (
  loop_id UUID NOT NULL REFERENCES evolution_loops(loop_id) ON DELETE CASCADE,
  step_key TEXT NOT NULL CHECK(step_key IN ('baseline','campaign')),
  output_id UUID NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(loop_id,step_key)
);""")


def downgrade() -> None:
    """Refuse to discard recorded handoffs or active worker ownership."""
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM evolution_loop_steps) OR EXISTS (
    SELECT 1 FROM evolution_loops WHERE lease_expires_at >= NOW()
  ) THEN
    RAISE EXCEPTION 'cannot downgrade 0054 with recorded steps or active loop leases';
  END IF;
END $$;
DROP TABLE evolution_loop_steps;
DROP INDEX ix_evolution_loop_dispatch;
ALTER TABLE evolution_loops
  DROP CONSTRAINT ck_evolution_loop_lease_tuple,
  DROP COLUMN lease_owner,
  DROP COLUMN lease_token,
  DROP COLUMN lease_expires_at,
  DROP COLUMN next_attempt_at;""")

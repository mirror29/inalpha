"""Persist generation proposal completion before deterministic candidate evaluation."""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """A proposal receipt and its measured cost are committed with hypothesis replacement."""
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_proposal_checkpoints (
  campaign_id UUID NOT NULL REFERENCES evolution_campaigns(campaign_id),
  generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 5),
  content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  cost_usd NUMERIC NOT NULL CHECK(cost_usd>=0),
  fallback_calls INTEGER NOT NULL CHECK(fallback_calls BETWEEN 0 AND 2),
  committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(campaign_id,generation)
);
CREATE TRIGGER evolution_proposal_checkpoints_immutable
BEFORE UPDATE OR DELETE ON evolution_proposal_checkpoints
FOR EACH ROW EXECUTE FUNCTION reject_evolution_data_snapshot_mutation();
""")


def downgrade() -> None:
    """Do not erase a persisted provider call receipt."""
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM evolution_proposal_checkpoints) THEN
    RAISE EXCEPTION 'cannot downgrade 0057 with committed generation proposals';
  END IF;
END $$;
DROP TABLE evolution_proposal_checkpoints;
""")

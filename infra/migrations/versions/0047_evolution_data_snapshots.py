"""Persist immutable bars and split boundaries for each E2 campaign."""

from __future__ import annotations

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create one canonical compressed dataset per campaign."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_data_snapshots (
  snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL UNIQUE REFERENCES evolution_campaigns(campaign_id) ON DELETE CASCADE,
  owner_account_id UUID NOT NULL,
  venue TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  from_ts TIMESTAMPTZ NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  canonical_version TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  compressed_payload BYTEA NOT NULL,
  bar_count INTEGER NOT NULL CHECK (bar_count>=5 AND bar_count<=10000),
  discovery_end INTEGER NOT NULL,
  validation_end INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (discovery_end>0 AND discovery_end<validation_end AND validation_end<bar_count),
  UNIQUE(owner_account_id,snapshot_id)
);
ALTER TABLE evolution_campaigns
ADD COLUMN data_snapshot_id UUID UNIQUE REFERENCES evolution_data_snapshots(snapshot_id);

CREATE OR REPLACE FUNCTION reject_evolution_data_snapshot_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'evolution data snapshots are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER evolution_data_snapshots_immutable
BEFORE UPDATE OR DELETE ON evolution_data_snapshots
FOR EACH ROW EXECUTE FUNCTION reject_evolution_data_snapshot_mutation();"""
    )


def downgrade() -> None:
    """Remove frozen E2 datasets after detaching campaign references."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_campaigns DROP COLUMN data_snapshot_id;
DROP TRIGGER evolution_data_snapshots_immutable ON evolution_data_snapshots;
DROP FUNCTION reject_evolution_data_snapshot_mutation();
DROP TABLE evolution_data_snapshots;"""
    )

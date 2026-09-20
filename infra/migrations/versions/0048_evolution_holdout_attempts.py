"""Make sealed holdout consumption durable and crash-resumable."""

from __future__ import annotations

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create one irreversible, fenced holdout attempt per campaign."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_holdout_attempts (
  attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL UNIQUE REFERENCES evolution_campaigns(campaign_id),
  owner_account_id UUID NOT NULL,
  candidate_id UUID NOT NULL REFERENCES evolution_implementations(implementation_id),
  status TEXT NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved','running','succeeded','failed')),
  fencing_token UUID,
  lease_expires_at TIMESTAMPTZ,
  passed BOOLEAN,
  evidence JSONB,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK ((status IN ('succeeded','failed')) = (finished_at IS NOT NULL)),
  CHECK ((status IN ('succeeded','failed')) = (passed IS NOT NULL)),
  UNIQUE(owner_account_id,attempt_id)
);
CREATE INDEX ix_evolution_holdout_attempts_dispatch
ON evolution_holdout_attempts(status,lease_expires_at,created_at)
WHERE status IN ('reserved','running');
CREATE OR REPLACE FUNCTION protect_evolution_holdout_attempt_identity() RETURNS trigger AS $$
BEGIN
  IF NEW.campaign_id <> OLD.campaign_id
     OR NEW.owner_account_id <> OLD.owner_account_id
     OR NEW.candidate_id <> OLD.candidate_id THEN
    RAISE EXCEPTION 'evolution holdout attempt identity is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER evolution_holdout_attempt_identity_immutable
BEFORE UPDATE ON evolution_holdout_attempts
FOR EACH ROW EXECUTE FUNCTION protect_evolution_holdout_attempt_identity();"""
    )


def downgrade() -> None:
    """Remove durable holdout attempts only when none have been consumed."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM evolution_holdout_attempts LIMIT 1) THEN
    RAISE EXCEPTION 'cannot downgrade 0048 while holdout attempts exist';
  END IF;
END $$;
DROP TRIGGER evolution_holdout_attempt_identity_immutable ON evolution_holdout_attempts;
DROP FUNCTION protect_evolution_holdout_attempt_identity();
DROP TABLE evolution_holdout_attempts;"""
    )

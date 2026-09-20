"""Make the event ledger revision-safe and add a durable extraction outbox."""

from __future__ import annotations

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Allow recurring content hashes and persist exactly-once extraction work."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE raw_market_events
DROP CONSTRAINT raw_market_events_source_source_event_id_content_hash_key;
ALTER TABLE market_event_facts
DROP CONSTRAINT market_event_facts_raw_event_id_fact_key_fact_hash_key;

CREATE TABLE event_extraction_jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  raw_event_id UUID NOT NULL UNIQUE REFERENCES raw_market_events(event_id),
  extractor_version TEXT NOT NULL DEFAULT 'deterministic-event-extractor-v1',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','leased','completed','dead')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 20),
  lease_owner TEXT,
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (
    (status='leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
      AND lease_expires_at IS NOT NULL)
    OR status<>'leased'
  )
);
CREATE INDEX ix_event_extraction_jobs_claim
ON event_extraction_jobs(next_attempt_at,created_at)
WHERE status IN ('pending','leased');

CREATE OR REPLACE FUNCTION reject_event_ledger_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'event ledger rows are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER raw_market_events_immutable
BEFORE UPDATE OR DELETE ON raw_market_events
FOR EACH ROW EXECUTE FUNCTION reject_event_ledger_mutation();
CREATE TRIGGER market_event_facts_immutable
BEFORE UPDATE OR DELETE ON market_event_facts
FOR EACH ROW EXECUTE FUNCTION reject_event_ledger_mutation();
CREATE TRIGGER market_event_snapshots_immutable
BEFORE UPDATE OR DELETE ON market_event_snapshots
FOR EACH ROW EXECUTE FUNCTION reject_event_ledger_mutation();
CREATE TRIGGER market_event_snapshot_facts_immutable
BEFORE UPDATE OR DELETE ON market_event_snapshot_facts
FOR EACH ROW EXECUTE FUNCTION reject_event_ledger_mutation();"""
    )


def downgrade() -> None:
    """Remove the outbox and restore the historical uniqueness constraints."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DROP TRIGGER market_event_snapshot_facts_immutable ON market_event_snapshot_facts;
DROP TRIGGER market_event_snapshots_immutable ON market_event_snapshots;
DROP TRIGGER market_event_facts_immutable ON market_event_facts;
DROP TRIGGER raw_market_events_immutable ON raw_market_events;
DROP FUNCTION reject_event_ledger_mutation();
DROP TABLE event_extraction_jobs;
ALTER TABLE raw_market_events
ADD CONSTRAINT raw_market_events_source_source_event_id_content_hash_key
UNIQUE(source,source_event_id,content_hash);
ALTER TABLE market_event_facts
ADD CONSTRAINT market_event_facts_raw_event_id_fact_key_fact_hash_key
UNIQUE(raw_event_id,fact_key,fact_hash);"""
    )

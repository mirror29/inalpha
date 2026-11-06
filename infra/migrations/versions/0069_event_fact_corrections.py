"""Retain immutable audit records for prospective legacy fact corrections."""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Link each authenticated review to its original and appended fact versions."""
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE market_event_fact_corrections (
 correction_id UUID PRIMARY KEY,
 source_fact_id UUID NOT NULL REFERENCES market_event_facts(fact_id),
 corrected_fact_id UUID NOT NULL UNIQUE REFERENCES market_event_facts(fact_id),
 retired_fact_id UUID REFERENCES market_event_facts(fact_id),
 request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
 service_subject TEXT NOT NULL CHECK (length(service_subject) BETWEEN 1 AND 500),
 reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
 available_at TIMESTAMPTZ NOT NULL,
 CHECK (source_fact_id <> corrected_fact_id)
);
""")


def downgrade() -> None:
    """Never discard an audit trail after corrections have been recorded."""
    op.execute("""SET LOCAL lock_timeout = '10s';
LOCK TABLE market_event_fact_corrections IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM market_event_fact_corrections) THEN
  RAISE EXCEPTION 'cannot discard recorded event fact corrections';
 END IF;
END $$;
DROP TABLE market_event_fact_corrections;
""")

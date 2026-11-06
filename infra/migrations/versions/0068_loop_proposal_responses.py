"""Checkpoint each loop proposal response before committing a complete generation."""
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_loop_proposal_responses (
 loop_id UUID NOT NULL REFERENCES evolution_loop_authorizations(loop_id),
 generation INTEGER NOT NULL CHECK (generation BETWEEN 1 AND 5),
 batch INTEGER NOT NULL CHECK (batch IN (0,1)),
 request_digest TEXT NOT NULL CHECK (length(request_digest)=64),
 reservation_id UUID NOT NULL UNIQUE REFERENCES evolution_loop_cost_reservations(reservation_id),
 response JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 responded_at TIMESTAMPTZ,
 PRIMARY KEY(loop_id,generation,batch),
 CHECK ((response IS NULL) = (responded_at IS NULL))
);
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM evolution_loop_proposal_responses) THEN
  RAISE EXCEPTION 'cannot discard recorded proposal attempts';
 END IF;
END $$;
DROP TABLE evolution_loop_proposal_responses;
""")

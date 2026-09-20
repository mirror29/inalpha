"""Persist bounded owner authorization separately from short-lived credential grants."""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store references and budgets only; never store a decrypted model key."""
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_loop_authorizations (
  loop_id UUID PRIMARY KEY REFERENCES evolution_loops(loop_id) ON DELETE CASCADE,
  owner_account_id UUID NOT NULL,
  request_digest TEXT NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
  llm_snapshot JSONB NOT NULL,
  max_cost_usd NUMERIC NOT NULL CHECK(max_cost_usd>0),
  spent_usd NUMERIC NOT NULL DEFAULT 0 CHECK(spent_usd>=0),
  reserved_usd NUMERIC NOT NULL DEFAULT 0 CHECK(reserved_usd>=0),
  authorized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  CHECK(expires_at>authorized_at),
  CHECK(spent_usd+reserved_usd<=max_cost_usd)
);
CREATE TABLE evolution_loop_cost_reservations (
  reservation_id UUID PRIMARY KEY,
  loop_id UUID NOT NULL REFERENCES evolution_loop_authorizations(loop_id) ON DELETE CASCADE,
  step_key TEXT NOT NULL,
  reserved_usd NUMERIC NOT NULL CHECK(reserved_usd>0),
  actual_usd NUMERIC CHECK(actual_usd>=0 AND actual_usd<=reserved_usd),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  settled_at TIMESTAMPTZ,
  CHECK((actual_usd IS NULL)=(settled_at IS NULL))
);
CREATE INDEX ix_evolution_loop_cost_reservations_loop
ON evolution_loop_cost_reservations(loop_id,created_at);
""")


def downgrade() -> None:
    """Do not erase an owner's authorization or cost audit trail."""
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM evolution_loop_authorizations) THEN
    RAISE EXCEPTION 'cannot downgrade 0055 with durable loop authorizations';
  END IF;
END $$;
DROP TABLE evolution_loop_cost_reservations;
DROP TABLE evolution_loop_authorizations;
""")

"""Bind evolution operation identities to trusted, owner-scoped chat invocations."""
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE chat_evolution_operations (
 auth_sub TEXT NOT NULL,
 operation_id UUID NOT NULL,
 invocation_id UUID NOT NULL,
 tool_name TEXT NOT NULL CHECK (tool_name IN ('evolver.run_evolution','evolver.start_evolution_loop','evolver.run_event_campaign')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(auth_sub,operation_id)
);
CREATE INDEX chat_evolution_invocation ON chat_evolution_operations(auth_sub,invocation_id);
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM chat_evolution_operations) THEN
  RAISE EXCEPTION 'cannot downgrade 0065 with recorded evolution chat associations';
 END IF;
END $$;
DROP TABLE chat_evolution_operations;
""")

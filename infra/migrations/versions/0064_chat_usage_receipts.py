"""Record owner-scoped individual chat calls without prompts or credentials."""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
CREATE TABLE chat_usage_receipts (
 call_id UUID PRIMARY KEY,
 invocation_id UUID NOT NULL,
 auth_sub TEXT NOT NULL,
 call_source TEXT NOT NULL CHECK (call_source IN ('chat','service')),
 step_number INTEGER NOT NULL CHECK (step_number >= 0),
 provider TEXT,
 model TEXT,
 config_id TEXT,
 pricing JSONB,
 usage_status TEXT NOT NULL DEFAULT 'unknown' CHECK (usage_status IN ('known','unknown')),
 input_tokens BIGINT CHECK (input_tokens >= 0),
 output_tokens BIGINT CHECK (output_tokens >= 0),
 cached_input_tokens BIGINT CHECK (cached_input_tokens >= 0),
 estimated_cost_usd NUMERIC(20,12) CHECK (estimated_cost_usd >= 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 settled_at TIMESTAMPTZ,
 CHECK (usage_status <> 'known' OR (input_tokens IS NOT NULL AND output_tokens IS NOT NULL)),
 CHECK (estimated_cost_usd IS NULL OR usage_status = 'known'),
 CHECK (cached_input_tokens IS NULL OR cached_input_tokens <= input_tokens)
);
CREATE INDEX chat_usage_owner_invocation ON chat_usage_receipts(auth_sub, invocation_id);
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM chat_usage_receipts) THEN
  RAISE EXCEPTION 'cannot downgrade 0064 with recorded chat calls';
 END IF;
END $$;
DROP TABLE chat_usage_receipts;
""")

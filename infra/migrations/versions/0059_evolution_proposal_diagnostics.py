"""Keep sanitized proposal rejection categories with immutable generation receipts."""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Record error categories without provider content, credentials or error messages."""
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_proposal_checkpoints
ADD COLUMN diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb
CHECK(jsonb_typeof(diagnostics)='array' AND jsonb_array_length(diagnostics)<=2);
""")


def downgrade() -> None:
    """Preserve diagnostic audit evidence once any proposal rejection has been recorded."""
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM evolution_proposal_checkpoints WHERE diagnostics!='[]'::jsonb) THEN
    RAISE EXCEPTION 'cannot downgrade 0059 with recorded proposal diagnostics';
  END IF;
END $$;
ALTER TABLE evolution_proposal_checkpoints DROP COLUMN diagnostics;
""")

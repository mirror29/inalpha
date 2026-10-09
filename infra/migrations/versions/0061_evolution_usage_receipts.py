"""Preserve unknown provider usage instead of treating it as zero cost."""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE strategy_evo_candidates ADD COLUMN usage_status TEXT NOT NULL DEFAULT 'legacy_unknown'
CHECK (usage_status IN ('not_called','known','unknown','legacy_unknown'));
UPDATE strategy_evo_candidates SET usage_status='known'
WHERE input_tokens > 0 AND output_tokens >= 0 AND llm_cost_usd IS NOT NULL;
ALTER TABLE strategy_evo_candidates ALTER COLUMN usage_status SET DEFAULT 'not_called';
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM strategy_evo_candidates WHERE usage_status IN ('unknown','not_called')) THEN
  RAISE EXCEPTION 'cannot downgrade 0061 with recorded usage uncertainty';
 END IF;
END $$;
ALTER TABLE strategy_evo_candidates DROP COLUMN usage_status;
""")

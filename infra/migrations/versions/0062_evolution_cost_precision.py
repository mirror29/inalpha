"""Retain sub-cent evolution costs without rounding them to zero."""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE strategy_evo_runs ALTER COLUMN llm_cost_usd TYPE NUMERIC(20,12);
ALTER TABLE strategy_evo_candidates ALTER COLUMN llm_cost_usd TYPE NUMERIC(20,12);
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM strategy_evo_runs WHERE llm_cost_usd <> round(llm_cost_usd,4))
 OR EXISTS(SELECT 1 FROM strategy_evo_candidates WHERE llm_cost_usd <> round(llm_cost_usd,4)) THEN
  RAISE EXCEPTION 'cannot downgrade 0062 with recorded high-precision costs';
 END IF;
END $$;
ALTER TABLE strategy_evo_runs ALTER COLUMN llm_cost_usd TYPE NUMERIC(10,4);
ALTER TABLE strategy_evo_candidates ALTER COLUMN llm_cost_usd TYPE NUMERIC(10,4);
""")

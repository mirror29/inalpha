"""Separate standalone research experiments from explicitly authorized retry attempts."""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
ALTER TABLE strategy_evo_runs
ADD COLUMN experiment_id UUID,
ADD COLUMN retry_of_run_id UUID REFERENCES strategy_evo_runs(run_id),
ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1 CHECK(attempt_number > 0);
UPDATE strategy_evo_runs SET experiment_id=run_id;
ALTER TABLE strategy_evo_runs ALTER COLUMN experiment_id SET NOT NULL;
ALTER TABLE strategy_evo_runs ALTER COLUMN experiment_id SET DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX strategy_evo_experiment_attempt ON strategy_evo_runs(experiment_id,attempt_number);
CREATE UNIQUE INDEX strategy_evo_experiment_active ON strategy_evo_runs(experiment_id)
WHERE status IN ('queued','running','cancelling');
""")


def downgrade() -> None:
    op.execute("""SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM strategy_evo_runs WHERE retry_of_run_id IS NOT NULL) THEN
  RAISE EXCEPTION 'cannot downgrade 0060 with recorded retry lineage';
 END IF;
END $$;
DROP INDEX strategy_evo_experiment_active;
DROP INDEX strategy_evo_experiment_attempt;
ALTER TABLE strategy_evo_runs DROP COLUMN attempt_number, DROP COLUMN retry_of_run_id, DROP COLUMN experiment_id;
""")

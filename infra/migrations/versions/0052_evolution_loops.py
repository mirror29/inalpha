"""Add owner-scoped durable EvolutionLoop orchestration records."""

from __future__ import annotations

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Persist stable target, operation and phase references across restarts."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
CREATE TABLE evolution_loops (
  loop_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_account_id UUID NOT NULL,
  requested_by_sub TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  target_kind TEXT NOT NULL CHECK (target_kind IN (
    'strategy_candidate','paper_runner','backtest_run','e1_run','e1_candidate','e2_campaign'
  )),
  target_id TEXT NOT NULL,
  target_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'target_resolved' CHECK (status IN (
    'target_resolved','baseline_ready','campaign_running','candidate_locked',
    'waiting_forward','holdout_running','adoption_ready','rejected',
    'insufficient_evidence','failed'
  )),
  e1_run_id UUID REFERENCES strategy_evo_runs(run_id),
  campaign_id UUID UNIQUE REFERENCES evolution_campaigns(campaign_id),
  forward_sandbox_id UUID REFERENCES paper_evolution_forward_sandboxes(sandbox_id),
  holdout_attempt_id UUID REFERENCES evolution_holdout_attempts(attempt_id),
  frozen_config JSONB NOT NULL,
  budget JSONB NOT NULL,
  failure_code TEXT,
  failure_message TEXT,
  state_version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  UNIQUE(owner_account_id,operation_id)
);
CREATE INDEX ix_evolution_loops_owner_updated
ON evolution_loops(owner_account_id,updated_at DESC,loop_id DESC);
CREATE INDEX ix_evolution_loops_active
ON evolution_loops(status,updated_at,loop_id)
WHERE status IN ('target_resolved','baseline_ready','campaign_running','candidate_locked',
                 'waiting_forward','holdout_running');
CREATE UNIQUE INDEX ux_evolution_loops_active_target
ON evolution_loops(owner_account_id,target_kind,target_id)
WHERE status IN ('target_resolved','baseline_ready','campaign_running','candidate_locked',
                 'waiting_forward','holdout_running');
CREATE TABLE evolution_loop_events (
  loop_id UUID NOT NULL REFERENCES evolution_loops(loop_id) ON DELETE CASCADE,
  version BIGINT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(loop_id,version)
);"""
    )


def downgrade() -> None:
    """Remove loops only when no durable workflow has been created."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM evolution_loops LIMIT 1) THEN
    RAISE EXCEPTION 'cannot downgrade 0052 while EvolutionLoop records exist';
  END IF;
END $$;
DROP TABLE evolution_loop_events;
DROP TABLE evolution_loops;"""
    )

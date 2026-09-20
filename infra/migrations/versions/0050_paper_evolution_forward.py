"""Add Paper-owned isolated Forward sandboxes for locked E2 champions."""

from __future__ import annotations

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Persist Forward identity, replay evidence and signed result projections."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
CREATE TABLE paper_evolution_forward_sandboxes (
  sandbox_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL UNIQUE REFERENCES evolution_campaigns(campaign_id),
  candidate_id UUID NOT NULL UNIQUE REFERENCES evolution_implementations(implementation_id),
  owner_account_id UUID NOT NULL,
  asset_id TEXT NOT NULL,
  venue TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL CHECK (timeframe IN ('15m','1h','4h')),
  source_code TEXT NOT NULL,
  source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
  hypothesis_spec JSONB NOT NULL,
  frozen_versions JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'observing'
    CHECK (status IN ('observing','passed','failed','insufficient_evidence')),
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deadline_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '90 days',
  event_count INTEGER NOT NULL DEFAULT 0 CHECK (event_count>=0),
  metrics JSONB,
  risk_alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
  data_quality_alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence_version BIGINT NOT NULL DEFAULT 0,
  evidence_digest TEXT,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK ((status='observing') = (finished_at IS NULL)),
  UNIQUE(owner_account_id,sandbox_id)
);
CREATE INDEX ix_paper_evolution_forward_dispatch
ON paper_evolution_forward_sandboxes(status,updated_at,sandbox_id)
WHERE status='observing';
CREATE TABLE paper_evolution_forward_bars (
  sandbox_id UUID NOT NULL REFERENCES paper_evolution_forward_sandboxes(sandbox_id),
  bar_open_at TIMESTAMPTZ NOT NULL,
  bar_known_at TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL,
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(sandbox_id,bar_open_at),
  CHECK (bar_known_at>bar_open_at)
);
CREATE TABLE paper_evolution_forward_facts (
  sandbox_id UUID NOT NULL REFERENCES paper_evolution_forward_sandboxes(sandbox_id),
  fact_id UUID NOT NULL REFERENCES market_event_facts(fact_id),
  available_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(sandbox_id,fact_id)
);
CREATE TABLE paper_evolution_forward_fills (
  sandbox_id UUID NOT NULL REFERENCES paper_evolution_forward_sandboxes(sandbox_id),
  fill_key TEXT NOT NULL,
  fact_id UUID,
  filled_at TIMESTAMPTZ NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity DOUBLE PRECISION NOT NULL CHECK (quantity>0),
  price DOUBLE PRECISION NOT NULL CHECK (price>0),
  fee DOUBLE PRECISION NOT NULL CHECK (fee>=0),
  slippage_cost DOUBLE PRECISION NOT NULL CHECK (slippage_cost>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(sandbox_id,fill_key)
);"""
    )


def downgrade() -> None:
    """Remove Paper Forward records only when no sandbox has been created."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM paper_evolution_forward_sandboxes LIMIT 1) THEN
    RAISE EXCEPTION 'cannot downgrade 0050 while Paper Forward sandboxes exist';
  END IF;
END $$;
DROP TABLE paper_evolution_forward_fills;
DROP TABLE paper_evolution_forward_facts;
DROP TABLE paper_evolution_forward_bars;
DROP TABLE paper_evolution_forward_sandboxes;"""
    )

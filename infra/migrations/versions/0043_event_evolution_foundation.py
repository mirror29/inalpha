"""Add the point-in-time market event ledger and E2 campaign records."""

from __future__ import annotations

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create immutable event versions, snapshots, and owner-scoped campaigns."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_credential_grant_uses
ADD COLUMN grant_purpose TEXT NOT NULL DEFAULT 'e1_run'
CHECK (grant_purpose IN ('e1_run','event_campaign')),
DROP CONSTRAINT evolution_credential_grant_uses_redemption_count_check,
ADD CONSTRAINT evolution_credential_grant_uses_redemption_count_check
CHECK (redemption_count BETWEEN 1 AND 8);
CREATE TABLE raw_market_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  title TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  url TEXT,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_valid_at TIMESTAMPTZ,
  claimed_published_at TIMESTAMPTZ,
  first_seen_at TIMESTAMPTZ NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL,
  collector_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  source_tier TEXT NOT NULL CHECK (source_tier IN ('official','professional_media','aggregator','structured')),
  supersedes_event_id UUID REFERENCES raw_market_events(event_id),
  retracted BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(source, source_event_id, version),
  UNIQUE(source, source_event_id, content_hash)
);
CREATE INDEX ix_raw_market_events_lookup
ON raw_market_events(source, source_event_id, version DESC);
CREATE INDEX ix_raw_market_events_first_seen
ON raw_market_events(first_seen_at, event_id);

CREATE TABLE market_event_facts (
  fact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  raw_event_id UUID NOT NULL REFERENCES raw_market_events(event_id),
  fact_key TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  fact_hash TEXT NOT NULL CHECK (fact_hash ~ '^[0-9a-f]{64}$'),
  event_type TEXT NOT NULL CHECK (event_type IN ('listing','delisting','exploit','chain_halt','regulatory','upgrade','unlock','burn','partnership','macro','other')),
  assets TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  actor TEXT,
  action TEXT NOT NULL,
  severity DOUBLE PRECISION NOT NULL CHECK (severity BETWEEN 0 AND 1),
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  effective_at TIMESTAMPTZ NOT NULL,
  available_at TIMESTAMPTZ NOT NULL,
  evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb,
  extractor_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  supersedes_fact_id UUID REFERENCES market_event_facts(fact_id),
  retracted BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(raw_event_id, fact_key, version),
  UNIQUE(raw_event_id, fact_key, fact_hash)
);
CREATE INDEX ix_market_event_facts_available
ON market_event_facts(available_at, fact_id);
CREATE INDEX ix_market_event_facts_assets
ON market_event_facts USING GIN(assets);

CREATE TABLE market_event_snapshots (
  snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cutoff TIMESTAMPTZ NOT NULL,
  policy_version TEXT NOT NULL,
  query_hash TEXT NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
  events_sha256 TEXT NOT NULL CHECK (events_sha256 ~ '^[0-9a-f]{64}$'),
  coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
  event_types TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  assets TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  fact_count INTEGER NOT NULL CHECK (fact_count >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(query_hash, events_sha256)
);
CREATE TABLE market_event_snapshot_facts (
  snapshot_id UUID NOT NULL REFERENCES market_event_snapshots(snapshot_id) ON DELETE CASCADE,
  fact_id UUID NOT NULL REFERENCES market_event_facts(fact_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  PRIMARY KEY(snapshot_id, fact_id),
  UNIQUE(snapshot_id, ordinal)
);

CREATE TABLE evolution_campaigns (
  campaign_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_account_id UUID NOT NULL,
  requested_by_sub TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  source_run_id UUID REFERENCES strategy_evo_runs(run_id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
    'draft','replaying','candidate_locked','waiting_forward','holdout_ready',
    'graduated','rejected','insufficient_evidence','failed','aborted'
  )),
  active_generation INTEGER NOT NULL DEFAULT 0 CHECK (active_generation BETWEEN 0 AND 5),
  hypothesis_budget INTEGER NOT NULL DEFAULT 8 CHECK (hypothesis_budget BETWEEN 1 AND 8),
  implementations_per_hypothesis INTEGER NOT NULL DEFAULT 3 CHECK (implementations_per_hypothesis = 3),
  max_generations INTEGER NOT NULL DEFAULT 5 CHECK (max_generations BETWEEN 1 AND 5),
  event_snapshot_id UUID NOT NULL REFERENCES market_event_snapshots(snapshot_id),
  frozen_config JSONB NOT NULL,
  llm_snapshot JSONB NOT NULL,
  llm_config_digest TEXT NOT NULL CHECK (llm_config_digest ~ '^[0-9a-f]{64}$'),
  llm_credential_grant TEXT CHECK (llm_credential_grant IS NULL OR length(llm_credential_grant) >= 100),
  llm_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (llm_cost_usd >= 0),
  locked_candidate_id UUID,
  holdout_consumed_at TIMESTAMPTZ,
  forward_started_at TIMESTAMPTZ,
  forward_deadline_at TIMESTAMPTZ,
  forward_event_count INTEGER NOT NULL DEFAULT 0 CHECK (forward_event_count >= 0),
  forward_metrics JSONB,
  failure_code TEXT,
  failure_message TEXT,
  lease_owner TEXT,
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  state_version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  CHECK ((status <> 'waiting_forward') OR (locked_candidate_id IS NOT NULL AND forward_started_at IS NOT NULL AND forward_deadline_at IS NOT NULL)),
  CHECK ((holdout_consumed_at IS NULL) OR locked_candidate_id IS NOT NULL)
  ,UNIQUE(owner_account_id,idempotency_key)
);
CREATE INDEX ix_evolution_campaigns_owner_created
ON evolution_campaigns(owner_account_id, created_at DESC, campaign_id DESC);
CREATE INDEX ix_evolution_campaigns_dispatch
ON evolution_campaigns(status, lease_expires_at, created_at)
WHERE status IN ('draft','replaying','holdout_ready');

CREATE TABLE evolution_hypotheses (
  hypothesis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES evolution_campaigns(campaign_id) ON DELETE CASCADE,
  generation INTEGER NOT NULL CHECK (generation BETWEEN 1 AND 5),
  slot INTEGER NOT NULL CHECK (slot BETWEEN 0 AND 7),
  lineage_kind TEXT NOT NULL CHECK (lineage_kind IN ('seed','elite','mutation','crossover','restart')),
  lane TEXT NOT NULL CHECK (lane IN ('event','event_regime','factor','execution_risk','regime','restart')),
  parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
  spec JSONB NOT NULL,
  spec_hash TEXT NOT NULL CHECK (spec_hash ~ '^[0-9a-f]{64}$'),
  upper_credit DOUBLE PRECISION,
  novelty_score DOUBLE PRECISION,
  pareto_rank INTEGER,
  selected BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(campaign_id, generation, slot),
  UNIQUE(campaign_id, generation, spec_hash)
);
CREATE INDEX ix_evolution_hypotheses_generation
ON evolution_hypotheses(campaign_id, generation, slot);

CREATE TABLE evolution_implementations (
  implementation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES evolution_campaigns(campaign_id) ON DELETE CASCADE,
  hypothesis_id UUID NOT NULL REFERENCES evolution_hypotheses(hypothesis_id) ON DELETE CASCADE,
  generation INTEGER NOT NULL CHECK (generation BETWEEN 1 AND 5),
  profile TEXT NOT NULL CHECK (profile IN ('direct','confirmed','hybrid','conservative','canonical','aggressive')),
  source_code TEXT NOT NULL,
  source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
  outcome TEXT NOT NULL DEFAULT 'pending' CHECK (outcome IN ('pending','succeeded','rejected','failed')),
  fitness DOUBLE PRECISION,
  validation_metrics JSONB,
  event_metrics JSONB,
  evidence_quality DOUBLE PRECISION,
  novelty_score DOUBLE PRECISION,
  fdr_pass BOOLEAN,
  error_code TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(hypothesis_id,profile)
);
CREATE INDEX ix_evolution_implementations_source_cache
ON evolution_implementations(campaign_id,source_hash)
WHERE outcome='succeeded';
CREATE INDEX ix_evolution_implementations_generation
ON evolution_implementations(campaign_id,generation,hypothesis_id);
ALTER TABLE evolution_campaigns ADD CONSTRAINT evolution_campaign_locked_candidate_fk
FOREIGN KEY(locked_candidate_id) REFERENCES evolution_implementations(implementation_id);

ALTER TABLE strategy_evo_candidates
ADD COLUMN campaign_id UUID REFERENCES evolution_campaigns(campaign_id),
ADD COLUMN hypothesis_id UUID REFERENCES evolution_hypotheses(hypothesis_id),
ADD COLUMN implementation_profile TEXT CHECK (implementation_profile IN ('direct','confirmed','hybrid','conservative','canonical','aggressive'));
CREATE INDEX ix_strategy_evo_candidates_campaign
ON strategy_evo_candidates(campaign_id,generation,slot)
WHERE campaign_id IS NOT NULL;

CREATE TABLE strategy_artifacts (
  artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_hash TEXT NOT NULL UNIQUE,
  source_code TEXT NOT NULL,
  dsl_spec JSONB,
  compiler_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE strategy_adoptions (
  adoption_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artifact_id UUID NOT NULL REFERENCES strategy_artifacts(artifact_id),
  owner_account_id UUID NOT NULL,
  campaign_id UUID REFERENCES evolution_campaigns(campaign_id),
  evidence_grade TEXT NOT NULL CHECK (evidence_grade IN ('standard','limited')),
  status TEXT NOT NULL DEFAULT 'experimental' CHECK (status IN ('experimental','accepted','rejected')),
  runner_eligible BOOLEAN NOT NULL DEFAULT FALSE CHECK (runner_eligible = FALSE),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  adopted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(owner_account_id, artifact_id)
);
CREATE INDEX ix_strategy_adoptions_owner
ON strategy_adoptions(owner_account_id, adopted_at DESC);
"""
    )


def downgrade() -> None:
    """Remove E2 records without changing the existing E1 schema."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM raw_market_events LIMIT 1)
     OR EXISTS (SELECT 1 FROM market_event_facts LIMIT 1)
     OR EXISTS (SELECT 1 FROM market_event_snapshots LIMIT 1)
     OR EXISTS (SELECT 1 FROM evolution_campaigns LIMIT 1)
     OR EXISTS (SELECT 1 FROM strategy_artifacts LIMIT 1)
     OR EXISTS (SELECT 1 FROM strategy_adoptions LIMIT 1)
     OR EXISTS (
       SELECT 1 FROM evolution_credential_grant_uses
       WHERE grant_purpose = 'event_campaign' LIMIT 1
     ) THEN
    RAISE EXCEPTION
      'cannot downgrade 0043 while E2 event or campaign records exist; archive and remove them first';
  END IF;
END $$;
DROP TABLE strategy_adoptions;
DROP TABLE strategy_artifacts;
ALTER TABLE evolution_campaigns DROP CONSTRAINT evolution_campaign_locked_candidate_fk;
ALTER TABLE strategy_evo_candidates
DROP COLUMN implementation_profile,
DROP COLUMN hypothesis_id,
DROP COLUMN campaign_id;
DROP TABLE evolution_implementations;
DROP TABLE evolution_hypotheses;
DROP TABLE evolution_campaigns;
DROP TABLE market_event_snapshot_facts;
DROP TABLE market_event_snapshots;
DROP TABLE market_event_facts;
DROP TABLE raw_market_events;
ALTER TABLE evolution_credential_grant_uses
DROP CONSTRAINT evolution_credential_grant_uses_redemption_count_check,
ADD CONSTRAINT evolution_credential_grant_uses_redemption_count_check
CHECK (redemption_count BETWEEN 1 AND 2),
DROP COLUMN grant_purpose;
"""
    )

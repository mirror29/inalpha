"""Add a unified, owner-scoped request ledger and bridge existing chat receipts."""
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
SET LOCAL lock_timeout = '10s';
CREATE TABLE llm_usage_calls (
 call_id UUID PRIMARY KEY,
 logical_call_id UUID NOT NULL,
 auth_sub TEXT NOT NULL CHECK (length(auth_sub)>0),
 service TEXT NOT NULL,
 stage TEXT NOT NULL,
 operation_id TEXT NOT NULL,
 parent_operation_id TEXT,
 attempt INTEGER NOT NULL CHECK (attempt >= 0),
 provider TEXT,
 model TEXT,
 response_model TEXT,
 config_id TEXT,
 sampling JSONB NOT NULL DEFAULT '{}',
 pricing JSONB,
 links JSONB NOT NULL DEFAULT '{}',
 normalization_version TEXT NOT NULL DEFAULT 'usage-v1',
 provenance TEXT NOT NULL DEFAULT 'request',
 status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','failed','cancelled','interrupted','invalid','truncated')),
 finish_reason TEXT,
 usage_status TEXT NOT NULL DEFAULT 'unknown' CHECK (usage_status IN ('known','unknown')),
 input_tokens BIGINT CHECK (input_tokens >= 0),
 output_tokens BIGINT CHECK (output_tokens >= 0),
 cached_input_tokens BIGINT CHECK (cached_input_tokens >= 0),
 cache_write_tokens BIGINT CHECK (cache_write_tokens >= 0),
 reasoning_tokens BIGINT CHECK (reasoning_tokens >= 0),
 estimated_cost_usd NUMERIC(20,12) CHECK (estimated_cost_usd >= 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 settled_at TIMESTAMPTZ,
 latency_ms BIGINT CHECK (latency_ms >= 0),
 UNIQUE (auth_sub,logical_call_id,attempt),
 CHECK (usage_status <> 'known' OR (input_tokens IS NOT NULL AND output_tokens IS NOT NULL)),
 CHECK (estimated_cost_usd IS NULL OR usage_status='known'),
 CHECK (cached_input_tokens IS NULL OR input_tokens IS NULL OR cached_input_tokens<=input_tokens),
 CHECK (reasoning_tokens IS NULL OR output_tokens IS NULL OR reasoning_tokens<=output_tokens)
);
CREATE INDEX llm_usage_owner_time ON llm_usage_calls(auth_sub,created_at DESC,call_id);
CREATE INDEX llm_usage_owner_operation ON llm_usage_calls(auth_sub,operation_id);
CREATE INDEX llm_usage_links ON llm_usage_calls USING GIN(links);

ALTER TABLE chat_usage_receipts ADD COLUMN reasoning_tokens BIGINT CHECK(reasoning_tokens>=0);
ALTER TABLE chat_usage_receipts ADD COLUMN request_status TEXT NOT NULL DEFAULT 'pending' CHECK(request_status IN ('pending','completed','failed','truncated'));
ALTER TABLE chat_usage_receipts ADD COLUMN finish_reason TEXT;
ALTER TABLE chat_usage_receipts ADD COLUMN response_model TEXT;
ALTER TABLE chat_usage_receipts ADD COLUMN sampling JSONB NOT NULL DEFAULT '{}';
-- The bridge makes old chat writers and their compatibility readers atomic with the ledger.
CREATE FUNCTION bridge_chat_usage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO llm_usage_calls(call_id,logical_call_id,auth_sub,service,stage,operation_id,
 attempt,provider,model,config_id,pricing,provenance,status,usage_status,input_tokens,
 output_tokens,cached_input_tokens,estimated_cost_usd,created_at,settled_at)
 VALUES(NEW.call_id,NEW.call_id,NEW.auth_sub,'orchestration',NEW.call_source,
 NEW.invocation_id::text,0,NEW.provider,NEW.model,NEW.config_id,NEW.pricing,'chat-receipt',
 CASE WHEN NEW.settled_at IS NULL THEN 'pending' ELSE 'completed' END,NEW.usage_status,
 NEW.input_tokens,NEW.output_tokens,NEW.cached_input_tokens,NEW.estimated_cost_usd,
 NEW.created_at,NEW.settled_at)
 ON CONFLICT(call_id) DO UPDATE SET usage_status=EXCLUDED.usage_status,
 input_tokens=EXCLUDED.input_tokens,output_tokens=EXCLUDED.output_tokens,
 cached_input_tokens=EXCLUDED.cached_input_tokens,estimated_cost_usd=EXCLUDED.estimated_cost_usd,
 settled_at=EXCLUDED.settled_at,status=EXCLUDED.status
 WHERE llm_usage_calls.auth_sub=EXCLUDED.auth_sub AND llm_usage_calls.settled_at IS NULL;
 UPDATE llm_usage_calls SET reasoning_tokens=NEW.reasoning_tokens,sampling=NEW.sampling,
 status=CASE WHEN NEW.request_status='pending' AND NEW.settled_at IS NOT NULL THEN 'completed' ELSE NEW.request_status END,
 finish_reason=NEW.finish_reason,response_model=NEW.response_model,
 latency_ms=CASE WHEN NEW.settled_at IS NOT NULL THEN
 GREATEST(0,(EXTRACT(EPOCH FROM (NEW.settled_at-NEW.created_at))*1000)::bigint) ELSE NULL END
 WHERE call_id=NEW.call_id AND auth_sub=NEW.auth_sub;
 RETURN NEW;
END $$;
CREATE TRIGGER chat_usage_bridge AFTER INSERT OR UPDATE ON chat_usage_receipts
 FOR EACH ROW EXECUTE FUNCTION bridge_chat_usage();
INSERT INTO llm_usage_calls(call_id,logical_call_id,auth_sub,service,stage,operation_id,
 attempt,provider,model,config_id,pricing,provenance,status,usage_status,input_tokens,
 output_tokens,cached_input_tokens,estimated_cost_usd,created_at,settled_at)
 SELECT call_id,call_id,auth_sub,'orchestration',call_source,invocation_id::text,0,
 provider,model,config_id,pricing,'legacy-chat-step',
 CASE WHEN settled_at IS NULL THEN 'pending' ELSE 'completed' END,
 usage_status,input_tokens,output_tokens,cached_input_tokens,estimated_cost_usd,created_at,settled_at
 FROM chat_usage_receipts ON CONFLICT(call_id) DO NOTHING;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM llm_usage_calls) THEN
  RAISE EXCEPTION 'cannot discard recorded LLM calls';
 END IF;
END $$;
DROP TRIGGER chat_usage_bridge ON chat_usage_receipts;
DROP FUNCTION bridge_chat_usage();
DROP TABLE llm_usage_calls;
ALTER TABLE chat_usage_receipts DROP COLUMN reasoning_tokens;
ALTER TABLE chat_usage_receipts DROP COLUMN sampling;
ALTER TABLE chat_usage_receipts DROP COLUMN request_status;
ALTER TABLE chat_usage_receipts DROP COLUMN finish_reason;
ALTER TABLE chat_usage_receipts DROP COLUMN response_model;
""")

"""0048 durable sealed-holdout migration tests."""

from __future__ import annotations

import psycopg
import pytest
from migration_0038_support import alembic, db_url

pytestmark = pytest.mark.integration


def test_0048_enforces_one_immutable_attempt_identity_per_campaign(
    migration_db_url: str,
) -> None:
    alembic(migration_db_url, "upgrade", "0048")
    owner = "81000000-0000-4000-8000-000000000001"
    snapshot = "81000000-0000-4000-8000-000000000002"
    campaign = "81000000-0000-4000-8000-000000000003"
    hypothesis = "81000000-0000-4000-8000-000000000004"
    candidate = "81000000-0000-4000-8000-000000000005"
    attempt = "81000000-0000-4000-8000-000000000006"
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO market_event_snapshots(
snapshot_id,cutoff,policy_version,query_hash,events_sha256,fact_count)
VALUES(%s,NOW(),'test-policy',%s,%s,0)""",
            (snapshot, "a" * 64, "b" * 64),
        )
        conn.execute(
            """INSERT INTO evolution_campaigns(
campaign_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,
event_snapshot_id,frozen_config,llm_snapshot,llm_config_digest)
VALUES(%s,%s,'user:test','holdout-test',%s,%s,'{}','{}',%s)""",
            (campaign, owner, "c" * 64, snapshot, "d" * 64),
        )
        conn.execute(
            """INSERT INTO evolution_hypotheses(
hypothesis_id,campaign_id,generation,slot,lineage_kind,lane,spec,spec_hash)
VALUES(%s,%s,1,0,'seed','event','{}',%s)""",
            (hypothesis, campaign, "e" * 64),
        )
        conn.execute(
            """INSERT INTO evolution_implementations(
implementation_id,campaign_id,hypothesis_id,generation,profile,source_code,source_hash)
VALUES(%s,%s,%s,1,'direct','pass',%s)""",
            (candidate, campaign, hypothesis, "f" * 64),
        )
        conn.execute(
            """INSERT INTO evolution_holdout_attempts(
attempt_id,campaign_id,owner_account_id,candidate_id)
VALUES(%s,%s,%s,%s)""",
            (attempt, campaign, owner, candidate),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                """INSERT INTO evolution_holdout_attempts(
campaign_id,owner_account_id,candidate_id) VALUES(%s,%s,%s)""",
                (campaign, owner, candidate),
            )
        with pytest.raises(psycopg.errors.RaiseException, match="identity is immutable"):
            conn.execute(
                """UPDATE evolution_holdout_attempts
SET candidate_id=%s WHERE attempt_id=%s""",
                ("81000000-0000-4000-8000-000000000007", attempt),
            )

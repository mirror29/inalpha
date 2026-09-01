"""0043 point-in-time event ledger and owner campaign integration tests."""

from __future__ import annotations

import psycopg
import pytest
from migration_0038_support import alembic, db_url

pytestmark = pytest.mark.integration


def test_0043_builds_event_campaign_schema_and_reversible_grant_scope(
    migration_db_url: str,
) -> None:
    """Exercise the critical foreign keys, one-shot records, and rolling downgrade."""
    alembic(migration_db_url, "upgrade", "0042")
    alembic(migration_db_url, "upgrade", "0043")

    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO evolution_credential_grant_uses
            (jti,owner_sub,config_id,operation_id,config_digest,request_digest,grant_purpose)
            VALUES ('44444444-4444-4444-8444-444444444443','user:alice','config-1',
                    'campaign-operation',%s,%s,'event_campaign')""",
            ("a" * 64, "b" * 64),
        )
        raw_id = conn.execute(
            """INSERT INTO raw_market_events(
            source,source_event_id,version,title,content_hash,first_seen_at,fetched_at,
            accepted_at,collector_version,policy_version,source_tier)
            VALUES('coinmarketcal','event-1',1,'listing',%s,NOW(),NOW(),NOW(),
                   'collector-v1','event-time-v1','structured') RETURNING event_id""",
            ("c" * 64,),
        ).fetchone()[0]
        fact_id = conn.execute(
            """INSERT INTO market_event_facts(
            raw_event_id,fact_key,version,fact_hash,event_type,assets,action,severity,
            confidence,effective_at,available_at,extractor_version,policy_version)
            VALUES(%s,'listing:btc',1,%s,'listing',ARRAY['BTC'],'listed',0.8,0.9,
                   NOW(),NOW(),'extractor-v1','event-time-v1') RETURNING fact_id""",
            (raw_id, "d" * 64),
        ).fetchone()[0]
        snapshot_id = conn.execute(
            """INSERT INTO market_event_snapshots(
            cutoff,policy_version,query_hash,events_sha256,fact_count)
            VALUES(NOW(),'event-time-v1',%s,%s,1) RETURNING snapshot_id""",
            ("e" * 64, "f" * 64),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO market_event_snapshot_facts(snapshot_id,fact_id,ordinal)
            VALUES(%s,%s,0)""",
            (snapshot_id, fact_id),
        )
        campaign_id = conn.execute(
            """INSERT INTO evolution_campaigns(
            owner_account_id,requested_by_sub,idempotency_key,request_hash,event_snapshot_id,
            frozen_config,llm_snapshot,llm_config_digest,llm_credential_grant)
            VALUES('00000000-0000-0000-0000-000000000099','user:alice','campaign-1',%s,%s,
                   '{}','{}',%s,%s) RETURNING campaign_id""",
            ("1" * 64, snapshot_id, "2" * 64, "grant-" + "x" * 120),
        ).fetchone()[0]
        hypothesis_id = conn.execute(
            """INSERT INTO evolution_hypotheses(
            campaign_id,generation,slot,lineage_kind,lane,spec,spec_hash)
            VALUES(%s,1,0,'seed','event','{}',%s) RETURNING hypothesis_id""",
            (campaign_id, "3" * 64),
        ).fetchone()[0]
        implementation_id = conn.execute(
            """INSERT INTO evolution_implementations(
            campaign_id,hypothesis_id,generation,profile,source_code,source_hash)
            VALUES(%s,%s,1,'direct','pass',%s) RETURNING implementation_id""",
            (campaign_id, hypothesis_id, "4" * 64),
        ).fetchone()[0]
        assert conn.execute(
            """SELECT i.implementation_id
            FROM evolution_implementations i
            JOIN evolution_hypotheses h ON h.hypothesis_id=i.hypothesis_id
            JOIN evolution_campaigns c ON c.campaign_id=i.campaign_id
            WHERE i.implementation_id=%s AND c.event_snapshot_id=%s""",
            (implementation_id, snapshot_id),
        ).fetchone() == (implementation_id,)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """UPDATE evolution_credential_grant_uses
                SET redemption_count=9 WHERE jti='44444444-4444-4444-8444-444444444443'"""
            )

    blocked = alembic(migration_db_url, "downgrade", "0042", check=False)
    assert blocked.returncode != 0
    assert "cannot downgrade 0043 while E2 event or campaign records exist" in (
        blocked.stderr
    )

    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        conn.execute("DELETE FROM evolution_campaigns")
        conn.execute("DELETE FROM market_event_snapshot_facts")
        conn.execute("DELETE FROM market_event_snapshots")
        conn.execute("DELETE FROM market_event_facts")
        conn.execute("DELETE FROM raw_market_events")
        conn.execute(
            "DELETE FROM evolution_credential_grant_uses WHERE grant_purpose='event_campaign'"
        )

    alembic(migration_db_url, "downgrade", "0042")
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('evolution_campaigns')").fetchone() == (
            None,
        )
        assert (
            conn.execute(
                """SELECT 1 FROM information_schema.columns
            WHERE table_name='evolution_credential_grant_uses'
              AND column_name='grant_purpose'"""
            ).fetchone()
            is None
        )

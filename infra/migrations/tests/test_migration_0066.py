"""Real PostgreSQL verifies compatibility bridging and duplicate settlement."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_ledger_migrates_and_atomically_bridges_chat(migration_db_url):
    alembic(migration_db_url, "upgrade", "0065")
    old, new = uuid4(), uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("INSERT INTO chat_usage_receipts(call_id,invocation_id,auth_sub,step_number,call_source) VALUES(%s,%s,'alice',0,'chat')", (old, uuid4()))
    alembic(migration_db_url, "upgrade", "0066")
    alembic(migration_db_url, "upgrade", "0066")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT provenance FROM llm_usage_calls WHERE call_id=%s", (old,)).fetchone() == ('legacy-chat-step',)
        conn.execute("INSERT INTO chat_usage_receipts(call_id,invocation_id,auth_sub,step_number,call_source) VALUES(%s,%s,'alice',0,'chat')", (new, uuid4()))
        assert conn.execute("SELECT status,input_tokens FROM llm_usage_calls WHERE call_id=%s", (new,)).fetchone() == ('pending', None)
        conn.execute("UPDATE chat_usage_receipts SET usage_status='known',input_tokens=10,output_tokens=20,reasoning_tokens=15,settled_at=NOW() WHERE call_id=%s", (new,))
        assert conn.execute("SELECT status,input_tokens,reasoning_tokens FROM llm_usage_calls WHERE call_id=%s", (new,)).fetchone() == ('completed', 10, 15)
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_calls WHERE auth_sub='alice'").fetchone() == (2,)


def test_request_records_prevent_destructive_rollback(migration_db_url):
    alembic(migration_db_url, "upgrade", "0066")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("INSERT INTO llm_usage_calls(call_id,logical_call_id,auth_sub,service,stage,operation_id,attempt) VALUES(%s,%s,'alice','research','pilot','run',0)", (uuid4(), uuid4()))
    result = alembic(migration_db_url, "downgrade", "0065", check=False)
    assert result.returncode != 0
    assert 'cannot discard recorded LLM calls' in result.stderr

"""Archive replay must retain source evidence and never backdate local revisions."""

import argparse
import runpy
from pathlib import Path

import pytest

module = runpy.run_path(str(Path(__file__).parents[1] / "replay-e2-archive.py"))
verify_provenance = module["verify_provenance"]
local_url = module["local_url"]
replay = module["replay"]


def original():
    return {
        "source": "coindesk",
        "source_event_id": "original-1",
        "content_hash": "a" * 64,
        "collector_version": "selected-news-forward@1",
        "policy_version": "first-seen-only-v1",
        "retracted": False,
        "source_valid_at": "2026-09-29T00:00:00Z",
        "claimed_published_at": None,
        "first_seen_at": "2026-09-30T01:00:00Z",
        "fetched_at": "2026-09-30T01:00:00Z",
        "accepted_at": "2026-09-30T01:00:00Z",
        "version": 1,
        "supersedes_event_id": None,
    }


def test_exact_original_provenance_and_timezone_equivalence():
    source = original()
    assert (
        verify_provenance(
            source, {**source, "accepted_at": "2026-09-30T09:00:00+08:00"}
        )
        is False
    )


@pytest.mark.parametrize(
    "field", ["content_hash", "source_event_id", "policy_version", "first_seen_at"]
)
def test_provenance_changes_fail_closed(field):
    source = original()
    with pytest.raises(ValueError):
        verify_provenance(source, {**source, field: "changed"})


def test_revision_visibility_may_move_later_but_not_earlier():
    source = original()
    revision = {
        **source,
        "version": 2,
        "supersedes_event_id": "previous",
        "accepted_at": "2026-09-30T02:00:00Z",
        "fetched_at": "2026-09-30T02:00:00Z",
    }
    assert verify_provenance(source, revision) is True
    with pytest.raises(ValueError, match="backdated"):
        verify_provenance(source, {**revision, "accepted_at": "2026-09-30T00:00:00Z"})
    with pytest.raises(ValueError, match="superseding"):
        verify_provenance(source, {**revision, "version": 1})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://user@localhost",
        "http://localhost/?token=x",
    ],
)
def test_service_tokens_cannot_be_sent_to_external_or_credentialed_urls(url):
    with pytest.raises(argparse.ArgumentTypeError):
        local_url(url)


def test_non_audit_database_rejected_before_network_or_database_access():
    with pytest.raises(ValueError, match="isolated"):
        replay(
            source_url="http://127.0.0.1:8001",
            destination_url="http://127.0.0.1:18011",
            database_url="postgresql://localhost/main",
            database_name="main",
            jwt_secret="unused",
        )


def test_same_database_rejected_before_access():
    with pytest.raises(ValueError, match="must differ"):
        replay(
            source_url="http://127.0.0.1:8001",
            destination_url="http://127.0.0.1:18011",
            database_url="postgresql://localhost/inalpha_e2_real_test",
            database_name="inalpha_e2_real_test",
            jwt_secret="unused",
        )


def test_misdirected_destination_rejected_before_any_write(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, *args):
            assert statement.startswith(("SELECT", "SET"))
            return self

        def fetchone(self):
            return {"event_id": "audit-event", "content_hash": "a" * 64}

        def fetchall(self):
            return []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content_hash": "b" * 64}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return Response()

        def post(self, *args, **kwargs):
            pytest.fail("routing mismatch must stop before ingestion")

    monkeypatch.setattr(
        module["psycopg"], "connect", lambda *args, **kwargs: Connection()
    )
    monkeypatch.setattr(module["httpx"], "Client", lambda *args, **kwargs: Client())
    with pytest.raises(ValueError, match="does not match"):
        replay(
            source_url="http://127.0.0.1:8001",
            destination_url="http://127.0.0.1:18011",
            database_url="postgresql://localhost/main",
            database_name="inalpha_e2_real_test",
            jwt_secret="a" * 32,
        )

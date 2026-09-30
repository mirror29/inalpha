"""Replay real archive records through local Data APIs into an isolated E2 audit DB."""

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

FIELDS = (
    "source",
    "source_event_id",
    "title",
    "content",
    "url",
    "raw_payload",
    "source_valid_at",
    "claimed_published_at",
    "first_seen_at",
    "fetched_at",
    "accepted_at",
    "collector_version",
    "policy_version",
    "source_tier",
    "retracted",
)


def local_url(value: str) -> str:
    """Confine service credentials and replay writes to explicit loopback HTTP services."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise argparse.ArgumentTypeError("service URL must be a loopback HTTP origin")
    return value.rstrip("/")


def headers(secret: str, purpose: str) -> dict[str, str]:
    """Mint the normal short-lived, audience/purpose-bound Data service identity."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "service:e2-archive-replay",
            "iat": now,
            "exp": now + 300,
            "token_use": "service",
            "service_audience": "data",
            "token_purpose": purpose,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": "Bearer " + token}


def verify_provenance(source: dict, destination: dict) -> bool:
    """Preserve source identity and first observation; revisions may only move visibility later."""
    for field in (
        "source",
        "source_event_id",
        "content_hash",
        "collector_version",
        "policy_version",
        "retracted",
    ):
        if source[field] != destination[field]:
            raise ValueError("replay changed source provenance")
    for field in (
        "source_valid_at",
        "claimed_published_at",
        "first_seen_at",
    ):
        before, after = source.get(field), destination.get(field)
        if before is None or after is None:
            if before != after:
                raise ValueError("replay changed original timestamps")
        elif datetime.fromisoformat(
            before.replace("Z", "+00:00")
        ) != datetime.fromisoformat(after.replace("Z", "+00:00")):
            raise ValueError("replay changed original timestamps")

    reobserved = False
    for field in ("fetched_at", "accepted_at"):
        before = datetime.fromisoformat(source[field].replace("Z", "+00:00"))
        after = datetime.fromisoformat(destination[field].replace("Z", "+00:00"))
        if after < before:
            raise ValueError("replay backdated revision visibility")
        if after > before:
            if destination["version"] <= 1 or not destination["supersedes_event_id"]:
                raise ValueError(
                    "only a superseding revision may have later local visibility"
                )
            reobserved = True
    return reobserved


def replay(
    *,
    source_url: str,
    destination_url: str,
    database_url: str,
    database_name: str,
    jwt_secret: str,
) -> dict:
    """Append genuine latest source records; an existing destination sentinel guards routing."""
    if (
        not database_name.startswith("inalpha_e2_real_")
        or source_url == destination_url
    ):
        raise ValueError("replay requires a separate isolated E2 audit destination")
    parsed = urlsplit(database_url.replace("postgresql+psycopg://", "postgresql://"))
    if parsed.path == "/" + database_name:
        raise ValueError("source and destination databases must differ")
    target_url = urlunsplit(parsed._replace(path="/" + database_name))
    cutoff = datetime.now(UTC)
    with psycopg.connect(target_url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='15s'")
        sentinel = conn.execute(
            "SELECT event_id,content_hash FROM raw_market_events ORDER BY event_id LIMIT 1"
        ).fetchone()
    if sentinel is None:
        raise ValueError(
            "destination must have an existing audit event to verify API routing"
        )
    with psycopg.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://"),
        row_factory=dict_row,
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='15s'")
        rows = conn.execute(
            """SELECT DISTINCT ON (source,source_event_id) event_id
FROM raw_market_events WHERE source IN ('coindesk','kraken_blog')
AND collector_version='selected-news-forward@1' AND policy_version='first-seen-only-v1'
AND accepted_at<=%s
ORDER BY source,source_event_id,accepted_at DESC,version DESC,event_id""",
            (cutoff,),
        ).fetchall()
    created = 0
    conservative_revisions = 0
    metadata = []
    with httpx.Client(timeout=60, trust_env=False) as client:
        response = client.get(
            destination_url + "/events/raw/" + str(sentinel["event_id"]),
            headers=headers(jwt_secret, "event_extract"),
        )
        response.raise_for_status()
        if response.json()["content_hash"] != sentinel["content_hash"]:
            raise ValueError("destination API does not match audit database")
        for row in rows:
            response = client.get(
                source_url + "/events/raw/" + str(row["event_id"]),
                headers=headers(jwt_secret, "event_extract"),
            )
            response.raise_for_status()
            raw = response.json()
            response = client.post(
                destination_url + "/events/raw",
                headers=headers(jwt_secret, "event_ingest"),
                json={key: raw[key] for key in FIELDS},
            )
            response.raise_for_status()
            result = response.json()
            conservative_revisions += int(verify_provenance(raw, result["event"]))
            created += int(result["created"])
            metadata.append(
                {
                    key: raw[key]
                    for key in (
                        "source",
                        "source_event_id",
                        "content_hash",
                        "accepted_at",
                        "retracted",
                    )
                }
            )
    digest = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "checked_at": cutoff.isoformat(),
        "source_records": len(rows),
        "raw_versions_created": created,
        "provenance_verified": len(rows),
        "conservatively_reobserved_revisions": conservative_revisions,
        "source_metadata_sha256": digest,
        "model_calls": 0,
        "extraction": "Use the normal Research worker; this command only ingests raw evidence.",
    }


def main() -> None:
    """Read local credentials without displaying them, and emit a sanitized replay receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env-file", required=True)
    parser.add_argument("--destination-database", required=True)
    parser.add_argument(
        "--source-data-url", type=local_url, default="http://127.0.0.1:8001"
    )
    parser.add_argument(
        "--destination-data-url", type=local_url, default="http://127.0.0.1:18011"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = dotenv_values(args.source_env_file)
    if not config.get("DATABASE_URL") or not config.get("JWT_SECRET"):
        parser.error("DATABASE_URL and JWT_SECRET are required")
    try:
        report = replay(
            source_url=args.source_data_url,
            destination_url=args.destination_data_url,
            database_url=config["DATABASE_URL"],
            database_name=args.destination_database,
            jwt_secret=config["JWT_SECRET"],
        )
    except (psycopg.Error, httpx.HTTPError, ValueError, KeyError):
        print(
            "Archive replay failed; inspect local service availability and routing. No credentials or provider payloads are printed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()

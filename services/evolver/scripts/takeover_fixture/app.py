"""Same-image Evolver probe: only peripheral HTTP fixtures and provider transport are replaced."""

import importlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import jwt
import psycopg
from inalpha_shared_llm import LLMClient
from openai import AsyncOpenAI
from psycopg.rows import dict_row

exchange = Path("/probe/exchange")
fixture = json.loads((exchange / "fixture.json").read_text())
secret = os.environ["JWT_SECRET"]
mutex = threading.Lock()


def append(name, value):
    with mutex:
        with (exchange / name).open("a") as out:
            out.write(value + "\n")
            out.flush()
            os.fsync(out.fileno())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/events/snapshots/" + fixture["snapshot"]["snapshot_id"]:
            claims = jwt.decode(
                self.headers.get("Authorization", "").removeprefix("Bearer "),
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            assert claims["token_purpose"] == "event_snapshot_read"
            append("requests", "snapshot")
            self.respond(fixture["snapshot"])
        elif self.path == "/api/internal/llm-config/config-1":
            assert self.headers.get("Authorization") == "Bearer isolated-test-grant"
            append("requests", "credential")
            self.respond(
                {"config_id": "config-1", "provider": "deepseek", "api_key": "offline-fixture-only"}
            )
        else:
            self.respond({}, 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/offline-provider":
            append("calls", os.environ["INSTANCE_STAGE"])
            deadline = time.monotonic() + 90
            while not (exchange / "release").exists():
                if time.monotonic() > deadline:
                    self.respond({}, 503)
                    return
                time.sleep(0.05)
            self.respond(
                {
                    "id": "offline-fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "deepseek-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "[{}, {}, {}, {}]"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                }
            )
        elif self.path == "/internal/evolution-loops/" + fixture["loop_id"] + "/credential-grant":
            claims = jwt.decode(
                self.headers.get("Authorization", "").removeprefix("Bearer "),
                secret,
                algorithms=["HS256"],
                audience="inalpha-orchestration",
            )
            assert (
                claims["token_purpose"] == "evolution_loop_credential"
                and claims["loop_id"] == fixture["loop_id"]
                and claims["owner_account_id"] == fixture["owner"]
            )
            db = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
            with psycopg.connect(db, row_factory=dict_row) as conn:
                conn.execute("BEGIN READ ONLY")
                conn.execute("SET LOCAL statement_timeout='5s'")
                assert conn.info.dbname.startswith("inalpha_container_audit_")
                row = conn.execute(
                    "SELECT lease_token,lease_expires_at>clock_timestamp() AS valid FROM evolution_campaigns WHERE campaign_id=%s",
                    (fixture["campaign_id"],),
                ).fetchone()
                assert row["valid"] and str(row["lease_token"]) == claims["lease_token"]
            append("requests", "local-fixture-grant")
            self.respond({"credential_grant": "isolated-test-grant"})
        else:
            self.respond({}, 404)


server = ThreadingHTTPServer(("127.0.0.1", 9011), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


async def isolated_client(self):
    if self._client is None:

        async def transport(request):
            assert request.url.host == "api.deepseek.com"
            async with httpx.AsyncClient(trust_env=False, timeout=120) as client:
                response = await client.post(
                    "http://127.0.0.1:9011/offline-provider", content=request.content
                )
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers={"content-type": "application/json"},
                request=request,
            )

        self._client = AsyncOpenAI(
            api_key="isolated-fixture-only",
            base_url=self.settings.llm_base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(transport), trust_env=False
            ),
        )
    return self._client


LLMClient._ensure_client = isolated_client

app = importlib.import_module("inalpha_evolver.main").app

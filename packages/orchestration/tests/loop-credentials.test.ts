import { generateKeyPairSync, randomUUID } from "node:crypto";
import { Hono } from "hono";
import { jwtVerify, SignJWT } from "jose";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { createLoopCredentialHandler } from "../src/evolution/credentials.js";
import { buildEvolutionLLMSnapshot } from "../src/mastra/llm/evolution-snapshot.js";

const secret = "test-loop-service-secret-at-least-32-chars";
const key = generateKeyPairSync("ed25519");
const scope = { loop_id: randomUUID(), owner_account_id: randomUUID(), lease_token: randomUUID(), phase: "baseline" };
const snapshot = buildEvolutionLLMSnapshot({ id: "config-test", provider: "deepseek", model: "deepseek-flash", api_key: "not-persisted" });

beforeEach(() => {
  setSettings({ jwtSecret: secret });
  vi.stubEnv("EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64", key.privateKey.export({ type: "pkcs8", format: "der" }).toString("base64"));
});
afterEach(() => { clearSettings(); vi.unstubAllEnvs(); });

async function token(overrides: Record<string, unknown> = {}, ttl = 300) {
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({
    sub: "service:evolver", aud: "inalpha-orchestration", iat: now, exp: now + ttl,
    token_use: "service", token_purpose: "evolution_loop_credential", ...scope, ...overrides,
  }).setProtectedHeader({ alg: "HS256" }).sign(new TextEncoder().encode(secret));
}

describe("durable loop credential renewal", () => {
  it("issues only a five-minute owner/config/operation/lease-bound grant", async () => {
    const lookup = vi.fn().mockResolvedValue({ requested_by_sub: "owner-sub", operation_id: "operation-loop", request_digest: "a".repeat(64), llm_snapshot: snapshot });
    const app = new Hono().post("/loops/:loopId", createLoopCredentialHandler(lookup));
    const response = await app.request(`/loops/${scope.loop_id}`, { method: "POST", headers: { Authorization: `Bearer ${await token()}` } });
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const { credential_grant: grant } = await response.json();
    const { payload } = await jwtVerify(grant, key.publicKey, { audience: "inalpha-dashboard-credential" });
    expect(payload).toMatchObject({ sub: "owner-sub", operation_id: "operation-loop", config_id: "config-test", loop_id: scope.loop_id, lease_token: scope.lease_token, loop_phase: "baseline" });
    expect(payload.exp! - payload.iat!).toBe(300);
    expect(JSON.stringify(payload)).not.toContain("not-persisted");
    expect(lookup).toHaveBeenCalledWith(scope);
  });

  it.each([
    { sub: "owner-sub" }, { aud: "inalpha-data" }, { token_use: "user" },
    { token_purpose: "other" }, { lease_token: "invalid" }, { phase: "holdout" },
    { loop_id: randomUUID() },
  ])("rejects invalid service scope before accessing stored authorization: %j", async (overrides) => {
    const lookup = vi.fn();
    const app = new Hono().post("/loops/:loopId", createLoopCredentialHandler(lookup));
    const response = await app.request(`/loops/${scope.loop_id}`, { method: "POST", headers: { Authorization: `Bearer ${await token(overrides)}` } });
    expect([401, 403]).toContain(response.status);
    expect(lookup).not.toHaveBeenCalled();
  });

  it("rejects excessive service TTL and absent or revoked authorization", async () => {
    const lookup = vi.fn().mockResolvedValue(undefined);
    const app = new Hono().post("/loops/:loopId", createLoopCredentialHandler(lookup));
    expect((await app.request(`/loops/${scope.loop_id}`, { method: "POST", headers: { Authorization: `Bearer ${await token({}, 301)}` } })).status).toBe(403);
    expect(lookup).not.toHaveBeenCalled();
    expect((await app.request(`/loops/${scope.loop_id}`, { method: "POST", headers: { Authorization: `Bearer ${await token()}` } })).status).toBe(403);
  });
});

import { generateKeyPairSync } from "node:crypto";

import { SignJWT } from "jose";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { decryptUserApiKey } from "@/lib/user-preferences";
import { getPool } from "@/lib/db";

import { GET } from "./route";

vi.mock("@/lib/user-preferences", () => ({
  decryptUserApiKey: vi.fn(),
}));
vi.mock("@/lib/db", () => ({ getPool: vi.fn() }));

const TEST_KEYS = generateKeyPairSync("ed25519");
const OTHER_KEYS = generateKeyPairSync("ed25519");
const PUBLIC_KEY_B64 = TEST_KEYS.publicKey.export({ format: "der", type: "spki" }).toString("base64");
const mockedDecryptUserApiKey = vi.mocked(decryptUserApiKey);
const mockedGetPool = vi.mocked(getPool);

/** Mints an isolated service credential token for this route test. */
async function token(
  overrides: Record<string, unknown> = {},
  options: { otherKey?: boolean; issuedAt?: number | null; expiresAt?: number } = {},
): Promise<string> {
  const now = Math.floor(Date.now() / 1_000);
  let builder = new SignJWT({
    token_use: "evolution_credential",
    config_id: "config-1",
    provider: "deepseek",
    operation_id: "operation-1",
    llm_config_digest: "a".repeat(64),
    request_digest: "b".repeat(64),
    ...overrides,
  })
    .setProtectedHeader({ alg: "EdDSA" })
    .setSubject("user:alice")
    .setAudience("inalpha-dashboard-credential")
    .setJti("11111111-1111-4111-8111-111111111111");
  if (options.issuedAt !== null) {
    builder = builder.setIssuedAt(options.issuedAt ?? now);
  }
  builder = builder.setExpirationTime(options.expiresAt ?? now + 300);
  return await builder.sign(options.otherKey ? OTHER_KEYS.privateKey : TEST_KEYS.privateKey);
}

/** Calls the dynamic route with a resolved Next.js params promise. */
async function callRoute(authorization?: string, id = "config-1") {
  const headers = authorization ? { Authorization: authorization } : undefined;
  return await GET(
    new NextRequest(`http://dashboard.test/api/internal/llm-config/${id}`, { headers }),
    { params: Promise.resolve({ id }) },
  );
}

beforeEach(() => {
  vi.stubEnv("EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64", PUBLIC_KEY_B64);
  mockedDecryptUserApiKey.mockReset();
  mockedGetPool.mockReset();
  mockedGetPool.mockReturnValue({
    query: vi.fn().mockResolvedValue({ rowCount: 1 }),
  } as never);
});

describe("internal owner LLM credential route", () => {
  it("rejects missing authentication and mismatched credential scope", async () => {
    expect((await callRoute()).status).toBe(401);
    expect((await callRoute(`Bearer ${await token({ config_id: "config-2" })}`)).status).toBe(
      403,
    );
    expect(mockedDecryptUserApiKey).not.toHaveBeenCalled();
  });

  it("rejects invalid, expired, or overlong service credentials", async () => {
    const now = Math.floor(Date.now() / 1_000);
    const requests = [
      callRoute(`Bearer ${await token({ token_use: "session" })}`),
      callRoute(`Bearer ${await token({ request_digest: "missing-scope" })}`),
      callRoute(`Bearer ${await token({}, { issuedAt: null })}`),
      callRoute(`Bearer ${await token({}, { issuedAt: now - 108_100, expiresAt: now + 1 })}`),
      callRoute(`Bearer ${await token({}, { issuedAt: now + 60, expiresAt: now + 120 })}`),
      callRoute(`Bearer ${await token({}, { issuedAt: now - 20, expiresAt: now - 10 })}`),
      callRoute(`Bearer ${await token({}, { otherKey: true })}`),
    ];

    expect((await Promise.all(requests)).map((response) => response.status)).toEqual([
      403,
      403,
      401,
      403,
      403,
      401,
      401,
    ]);
    expect(mockedDecryptUserApiKey).not.toHaveBeenCalled();
  });

  it("allows one exact-scope replay for a lost response, then rejects the grant", async () => {
    const query = vi
      .fn()
      .mockResolvedValueOnce({ rowCount: 1 })
      .mockResolvedValueOnce({ rowCount: 1 })
      .mockResolvedValueOnce({ rowCount: 0 });
    mockedGetPool.mockReturnValue({ query } as never);
    mockedDecryptUserApiKey.mockResolvedValue({
      id: "config-1",
      provider: "deepseek",
      api_key: "owner-key",
    } as never);
    const grant = await token();

    expect((await callRoute(`Bearer ${grant}`)).status).toBe(200);
    expect((await callRoute(`Bearer ${grant}`)).status).toBe(200);
    expect((await callRoute(`Bearer ${grant}`)).status).toBe(409);
    expect(mockedDecryptUserApiKey).toHaveBeenCalledTimes(3);
  });

  it("allows bounded campaign recovery redemptions for the grant lifetime", async () => {
    const query = vi.fn().mockResolvedValue({ rowCount: 1 });
    mockedGetPool.mockReturnValue({ query } as never);
    mockedDecryptUserApiKey.mockResolvedValue({
      id: "config-1",
      provider: "deepseek",
      api_key: "owner-key",
    } as never);

    expect(
      (await callRoute(`Bearer ${await token({ grant_purpose: "event_campaign" })}`)).status,
    ).toBe(200);
    expect(query.mock.calls[0]?.[1]).toEqual([
      "11111111-1111-4111-8111-111111111111",
      "user:alice",
      "config-1",
      "operation-1",
      "a".repeat(64),
      "b".repeat(64),
      "event_campaign",
      8,
      "30 hours",
    ]);
  });

  it("rejects a reused jti whose recorded scope differs", async () => {
    mockedGetPool.mockReturnValue({
      query: vi.fn().mockResolvedValue({ rowCount: 0 }),
    } as never);
    mockedDecryptUserApiKey.mockResolvedValue({
      id: "config-1",
      provider: "deepseek",
      api_key: "owner-key",
    } as never);

    expect((await callRoute(`Bearer ${await token()}`)).status).toBe(409);
  });

  it("returns only the requested owner's decrypted config without caching", async () => {
    mockedDecryptUserApiKey.mockResolvedValue({
      id: "config-1",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      custom_base_url: "https://api.deepseek.com",
      api_key: "owner-key",
      api_key_encrypted: "encrypted",
      api_key_nonce: "nonce",
      api_key_tag: "tag",
      created_at: "2026-08-26T00:00:00Z",
      updated_at: "2026-08-26T00:00:00Z",
    });

    const response = await callRoute(`Bearer ${await token()}`);

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({
      config_id: "config-1",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      base_url: "https://api.deepseek.com",
      api_key: "owner-key",
    });
    expect(mockedDecryptUserApiKey).toHaveBeenCalledWith("user:alice", "config-1");
  });

  it("does not fall back to another config when the reference no longer exists", async () => {
    mockedDecryptUserApiKey.mockResolvedValue(null);

    expect((await callRoute(`Bearer ${await token()}`)).status).toBe(404);
  });

  it("returns a retryable error when the encrypted credential store is unavailable", async () => {
    mockedDecryptUserApiKey.mockRejectedValue(new Error("database unavailable"));

    expect((await callRoute(`Bearer ${await token()}`)).status).toBe(503);
    expect(mockedGetPool).not.toHaveBeenCalled();
  });
});

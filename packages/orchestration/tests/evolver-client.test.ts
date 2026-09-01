import { generateKeyPairSync } from "node:crypto";

import { jwtVerify } from "jose";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildEvolutionStartRequest,
  buildEventCampaignRequest,
  eventCampaignRequestDigest,
  evolutionRequestDigest,
  EvolverClient,
} from "../src/clients/evolver.js";
import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";
import {
  APPROVAL_OPERATION_ID_KEY,
  buildEvolutionLLMSnapshot,
  USER_LLM_SNAPSHOT_KEY,
} from "../src/mastra/llm/evolution-snapshot.js";
import {
  getApprovedEvolutionRunContext,
  getApprovedEventCampaignContext,
} from "../src/tools/evolver-shared.js";

const snapshot = buildEvolutionLLMSnapshot({
  id: "config-1",
  provider: "deepseek",
  model: "deepseek-v4-pro",
  api_key: "not-forwarded",
});

function response(status: number): Response {
  return new Response(
    JSON.stringify(
      status === 200
        ? { run_id: "run-1", status: "queued" }
        : { code: `HTTP_${status}`, message: "temporary upstream failure" },
    ),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function campaignResponse(
  status = 200,
  campaignStatus = "replaying",
): Response {
  return new Response(
    JSON.stringify({
      campaign_id: "33333333-3333-4333-8333-333333333333",
      status: campaignStatus,
      active_generation: campaignStatus === "draft" ? 0 : 1,
      max_generations: 5,
      event_snapshot_id: "11111111-1111-4111-8111-111111111111",
      llm_cost_usd: 0,
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

function campaignConflict(): Response {
  return new Response(
    JSON.stringify({
      code: "CAMPAIGN_STATE_CONFLICT",
      message: "campaign cannot start",
    }),
    { status: 409, headers: { "Content-Type": "application/json" } },
  );
}

function options() {
  const request = buildEvolutionStartRequest({
    budget: 1,
    seedStrategyId: "sma_cross_v1",
    llmSnapshot: snapshot,
    config: {
      venue: "binance",
      symbol: "BTCUSDT",
      timeframe: "1h",
      from_ts: "2026-08-01T00:00:00Z",
      as_of: "2026-08-02T00:00:00Z",
      initial_cash: 10_000,
    },
  });
  return {
    request,
    idempotencyKey: "approval-operation-1",
    credentialGrant: "credential-grant",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("EvolverClient", () => {
  it("mints one Ed25519 grant bound to owner, operation, snapshot, and request", async () => {
    const keys = generateKeyPairSync("ed25519");
    vi.stubEnv(
      "EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64",
      keys.privateKey.export({ format: "der", type: "pkcs8" }).toString("base64"),
    );
    const requestContext = new Map<string, unknown>([
      [AUTH_SUB_KEY, "user:alice"],
      [APPROVAL_OPERATION_ID_KEY, "approval-operation-1"],
      [USER_LLM_SNAPSHOT_KEY, snapshot],
    ]);

    const input = {
      budget: 1,
      seedStrategyId: "sma_cross_v1",
      config: options().request.config,
    };
    const approved = await getApprovedEvolutionRunContext(input, requestContext);
    const { payload: credential } = await jwtVerify(
      approved.credentialGrant,
      keys.publicKey,
      { algorithms: ["EdDSA"], audience: "inalpha-dashboard-credential" },
    );

    expect(credential).toMatchObject({
      sub: "user:alice",
      token_use: "evolution_credential",
      config_id: "config-1",
      operation_id: "approval-operation-1",
      llm_config_digest: snapshot.config_digest,
      request_digest: evolutionRequestDigest(approved.request),
    });
    expect(Number(credential.exp) - Number(credential.iat)).toBe(108_000);
  });

  it("binds an approved E2 campaign grant to the shared durable operation identity", async () => {
    const keys = generateKeyPairSync("ed25519");
    vi.stubEnv(
      "EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64",
      keys.privateKey.export({ format: "der", type: "pkcs8" }).toString("base64"),
    );
    const requestContext = new Map<string, unknown>([
      [AUTH_SUB_KEY, "user:alice"],
      [APPROVAL_OPERATION_ID_KEY, "approval-operation-e2"],
      [USER_LLM_SNAPSHOT_KEY, snapshot],
    ]);
    const config = {
      venue: "binance",
      symbol: "BTCUSDT",
      timeframe: "1h" as const,
      from_ts: "2026-08-01T00:00:00Z",
      as_of: "2026-08-02T00:00:00Z",
    };

    const campaign = await getApprovedEventCampaignContext(
      { eventSnapshotId: "11111111-1111-4111-8111-111111111111", config },
      requestContext,
    );
    const { payload } = await jwtVerify(campaign.credentialGrant, keys.publicKey, {
      algorithms: ["EdDSA"],
      audience: "inalpha-dashboard-credential",
    });

    expect(payload).toMatchObject({
      sub: "user:alice",
      grant_purpose: "event_campaign",
      operation_id: "approval-operation-e2",
      llm_config_digest: snapshot.config_digest,
      request_digest: eventCampaignRequestDigest(campaign.request),
    });
    expect(campaign.operationId).toBe("approval-operation-e2");
    expect(campaign.request).toEqual(
      buildEventCampaignRequest({
        eventSnapshotId: "11111111-1111-4111-8111-111111111111",
        config,
        llmSnapshot: snapshot,
      }),
    );
  });

  it("recovers a lost start response by returning the already-started campaign on whole-operation retry", async () => {
    const request = buildEventCampaignRequest({
      eventSnapshotId: "11111111-1111-4111-8111-111111111111",
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
      llmSnapshot: snapshot,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(campaignResponse(201, "draft"))
      .mockRejectedValueOnce(new TypeError("start response lost"))
      .mockResolvedValueOnce(campaignResponse(201, "replaying"));
    vi.stubGlobal("fetch", fetchMock);

    const client = new EvolverClient({
      baseUrl: "http://evolver.test",
      token: "owner-token",
    });
    const options = {
      request,
      idempotencyKey: "approval-operation-e2",
      credentialGrant: "event-campaign-grant",
    };

    await expect(client.startEventCampaign(options)).rejects.toMatchObject({
      code: "UPSTREAM_UNREACHABLE",
    });
    await expect(client.startEventCampaign(options)).resolves.toMatchObject({
      campaign_id: "33333333-3333-4333-8333-333333333333",
      status: "replaying",
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const index of [0, 2]) {
      const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
      expect(url).toContain("/api/v1/campaigns");
      expect(url).not.toContain("/start");
      expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe(
        "approval-operation-e2",
      );
      expect((init.headers as Record<string, string>)["X-Evolution-Credential"]).toBe(
        "event-campaign-grant",
      );
      expect(init.body).not.toContain("not-forwarded");
    }
  });

  it("reconciles a concurrent E2 start conflict when the campaign already advanced", async () => {
    const request = buildEventCampaignRequest({
      eventSnapshotId: "11111111-1111-4111-8111-111111111111",
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
      llmSnapshot: snapshot,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(campaignResponse(201, "draft"))
      .mockResolvedValueOnce(campaignConflict())
      .mockResolvedValueOnce(campaignResponse(200, "replaying"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new EvolverClient({
        baseUrl: "http://evolver.test",
        token: "owner-token",
      }).startEventCampaign({
        request,
        idempotencyKey: "approval-operation-e2",
        credentialGrant: "event-campaign-grant",
      }),
    ).resolves.toMatchObject({ status: "replaying" });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("/start");
    expect(String(fetchMock.mock.calls[2]?.[0])).not.toContain("/start");
  });

  it("preserves E2 start conflicts when reconciliation still finds a draft campaign", async () => {
    const request = buildEventCampaignRequest({
      eventSnapshotId: "11111111-1111-4111-8111-111111111111",
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
      llmSnapshot: snapshot,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(campaignResponse(201, "draft"))
      .mockResolvedValueOnce(campaignConflict())
      .mockResolvedValueOnce(campaignResponse(200, "draft"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new EvolverClient({
        baseUrl: "http://evolver.test",
        token: "owner-token",
      }).startEventCampaign({
        request,
        idempotencyKey: "approval-operation-e2",
        credentialGrant: "event-campaign-grant",
      }),
    ).rejects.toMatchObject({
      code: "CAMPAIGN_STATE_CONFLICT",
      status: 409,
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("retries 502/504 with the same approval-derived operation ID", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(504))
      .mockResolvedValueOnce(response(200));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new EvolverClient({
      baseUrl: "http://evolver.test",
      token: "owner-token",
    }).startRun(options());

    expect(result.status).toBe("queued");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe(
        "approval-operation-1",
      );
      expect((init.headers as Record<string, string>)["X-Evolution-Approval"]).toBeUndefined();
      expect((init.headers as Record<string, string>)["X-Evolution-Credential"]).toBe(
        "credential-grant",
      );
      expect(init.body).not.toContain("not-forwarded");
    }
  });

  it("normalizes defaults and timezone offsets before computing the request digest", () => {
    const request = buildEvolutionStartRequest({
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        timeframe: "1h",
        from_ts: "2026-08-01T08:00:00+08:00",
        as_of: "2026-08-02T08:00:00+08:00",
      },
      llmSnapshot: snapshot,
    });

    expect(request.config).toMatchObject({
      from_ts: "2026-08-01T00:00:00.000Z",
      as_of: "2026-08-02T00:00:00.000Z",
      initial_cash: 10_000,
      fee_rate: 0.001,
      validation_split: 0.3,
    });
    expect(evolutionRequestDigest(request)).toBe(
      "0dd54caf902703e4303f252066bd9ad3ec1525c67cdab5bf1353befaefb48a2e",
    );
  });

  it("retries a 502 once but does not retry other client errors", async () => {
    const retryable = vi
      .fn()
      .mockResolvedValueOnce(response(502))
      .mockResolvedValueOnce(response(200));
    vi.stubGlobal("fetch", retryable);
    await expect(
      new EvolverClient({ baseUrl: "http://evolver.test", token: "owner-token" }).startRun(
        options(),
      ),
    ).resolves.toMatchObject({ status: "queued" });
    expect(retryable).toHaveBeenCalledTimes(2);

    const nonRetryable = vi.fn().mockResolvedValue(response(403));
    vi.stubGlobal("fetch", nonRetryable);
    await expect(
      new EvolverClient({ baseUrl: "http://evolver.test", token: "owner-token" }).startRun(
        options(),
      ),
    ).rejects.toThrow();
    expect(nonRetryable).toHaveBeenCalledTimes(1);
  });
});

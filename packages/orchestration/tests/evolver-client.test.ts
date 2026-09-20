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
import { clearSettings, setSettings } from "../src/config.js";
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
import {
  evolverRunEventCampaignTool,
} from "../src/tools/evolver.js";

const snapshot = buildEvolutionLLMSnapshot({
  id: "config-1",
  provider: "deepseek",
  model: "deepseek-flash",
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

function loopResponse(status = 201, loopStatus = "campaign_running"): Response {
  return new Response(
    JSON.stringify({
      loop_id: "44444444-4444-4444-8444-444444444444",
      operation_id: "approval-operation-e2",
      target_kind: "e1_run",
      target_id: "22222222-2222-4222-8222-222222222222",
      status: loopStatus,
      e1_run_id: "22222222-2222-4222-8222-222222222222",
      campaign_id: "33333333-3333-4333-8333-333333333333",
      forward_sandbox_id: null,
      holdout_attempt_id: null,
      failure_code: null,
      failure_message: null,
      state_version: 1,
    }),
    { status, headers: { "Content-Type": "application/json" } },
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
  clearSettings();
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
      asset_id: "asset:BTC",
      event_asset_code: "BTC",
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

  it("freezes a multi-source event snapshot before minting the approved campaign request", async () => {
    const keys = generateKeyPairSync("ed25519");
    vi.stubEnv(
      "EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64",
      keys.privateKey.export({ format: "der", type: "pkcs8" }).toString("base64"),
    );
    setSettings({
      dataServiceUrl: "http://data.test",
      paperServiceUrl: "http://paper.test",
      researchServiceUrl: "http://research.test",
      factorServiceUrl: "http://factor.test",
      evolverServiceUrl: "http://evolver.test",
      jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
      jwtAlgorithm: "HS256",
      schedulerEnabled: false,
      consoleSubject: "console:dev",
    });
    const requestContext = new Map<string, unknown>([
      [AUTH_SUB_KEY, "user:alice"],
      [APPROVAL_OPERATION_ID_KEY, "approval-operation-e2"],
      [USER_LLM_SNAPSHOT_KEY, snapshot],
    ]);
    const calls: Array<{ url: string; body: Record<string, unknown>; headers: Headers }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      calls.push({ url, body, headers: new Headers(init?.headers) });
      if (url === "http://evolver.test/api/v1/capabilities") {
        return new Response(JSON.stringify({
          event_evolution_enabled: true,
          reason: null,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.startsWith("http://data.test/assets/resolve")) {
        return new Response(JSON.stringify({
          asset_id: "asset:BTC",
          venue: "binance",
          symbol: "BTC/USDT:USDT",
          event_asset_code: "BTC",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === "http://data.test/events/snapshots") {
        return new Response(JSON.stringify({
          snapshot_id: "11111111-1111-4111-8111-111111111111",
          cutoff: "2026-08-02T00:00:00Z",
          policy_version: "all-visible-facts-v1",
          query_hash: "a".repeat(64),
          events_sha256: "b".repeat(64),
          coverage: { complete: true },
          event_types: ["listing", "delisting", "exploit", "chain_halt"],
          assets: ["BTC"],
          fact_count: 2,
          created_at: "2026-08-02T00:00:00Z",
          facts: [],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        campaign_id: "22222222-2222-4222-8222-222222222222",
        status: "draft",
      }), { status: 201, headers: { "Content-Type": "application/json" } });
    }));

    await evolverRunEventCampaignTool.execute!({
      sourceRunId: "33333333-3333-4333-8333-333333333333",
      config: {
        venue: "binance",
        symbol: "BTC/USDT:USDT",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
    } as never, { requestContext } as never);

    expect(calls).toHaveLength(4);
    expect(calls[0]).toMatchObject({
      url: "http://evolver.test/api/v1/capabilities",
    });
    expect(calls[1]).toMatchObject({
      url: expect.stringContaining("http://data.test/assets/resolve"),
    });
    expect(calls[2]).toMatchObject({
      url: "http://data.test/events/snapshots",
      body: {
        cutoff: "2026-08-02T00:00:00Z",
        policy_version: "all-visible-facts-v1",
        assets: ["BTC"],
      },
    });
    expect(calls[3]).toMatchObject({
      url: "http://evolver.test/api/v1/evolution-loops",
      body: {
        event_snapshot_id: "11111111-1111-4111-8111-111111111111",
        source_run_id: "33333333-3333-4333-8333-333333333333",
        config: expect.objectContaining({
          asset_id: "asset:BTC",
          event_asset_code: "BTC",
        }),
      },
    });
    expect(calls[3]?.headers.get("Idempotency-Key")).toBe("approval-operation-e2");
  });

  it("stops before campaign LLM spend when the automatic event snapshot is empty", async () => {
    setSettings({
      dataServiceUrl: "http://data.test",
      paperServiceUrl: "http://paper.test",
      researchServiceUrl: "http://research.test",
      factorServiceUrl: "http://factor.test",
      evolverServiceUrl: "http://evolver.test",
      jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
      jwtAlgorithm: "HS256",
      schedulerEnabled: false,
      consoleSubject: "console:dev",
    });
    const fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(
      url === "http://evolver.test/api/v1/capabilities"
        ? { event_evolution_enabled: true, reason: null }
        : url.startsWith("http://data.test/assets/resolve")
          ? {
              asset_id: "asset:BTC",
              venue: "binance",
              symbol: "BTCUSDT",
              event_asset_code: "BTC",
            }
          : {
            snapshot_id: "11111111-1111-4111-8111-111111111111",
            fact_count: 0,
          },
    ), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(evolverRunEventCampaignTool.execute!({
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        asset_id: "asset:BTC",
        event_asset_code: "BTC",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
    } as never, {
      requestContext: new Map([[AUTH_SUB_KEY, "user:alice"]]),
    } as never)).rejects.toThrow(
      "EVENT_SNAPSHOT_EMPTY",
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("creates or reuses one automatic E2 loop with the approval-derived operation", async () => {
    const request = buildEventCampaignRequest({
      eventSnapshotId: "11111111-1111-4111-8111-111111111111",
      config: {
        venue: "binance",
        symbol: "BTCUSDT",
        asset_id: "asset:BTC",
        event_asset_code: "BTC",
        timeframe: "1h",
        from_ts: "2026-08-01T00:00:00Z",
        as_of: "2026-08-02T00:00:00Z",
      },
      llmSnapshot: snapshot,
    });
    const fetchMock = vi.fn().mockResolvedValue(loopResponse());
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

    await expect(client.startEventCampaign(options)).resolves.toMatchObject({
      loop_id: "44444444-4444-4444-8444-444444444444",
      campaign_id: "33333333-3333-4333-8333-333333333333",
      status: "campaign_running",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/evolution-loops");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe(
      "approval-operation-e2",
    );
    expect((init.headers as Record<string, string>)["X-Evolution-Credential"]).toBe(
      "event-campaign-grant",
    );
    expect(init.body).not.toContain("not-forwarded");
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
      "455bdf12b70dd079ef7b90c991b3f815d48e0ecf7c12f1fcf38f2a554cce0005",
    );
  });

  it("binds nested strategy parameters using the Python-compatible numeric contract", () => {
    const request = buildEvolutionStartRequest({
      config: {
        venue: "binance", symbol: "BTCUSDT", timeframe: "1h",
        from_ts: "2026-07-13T12:00:00Z", as_of: "2026-08-12T12:00:00Z",
        params: { trade_size: 3, nested: [true, null, 0.5] },
      }, llmSnapshot: snapshot,
    });
    expect(evolutionRequestDigest(request)).toBe("a0fd1c7682bd74ed9e6a8466342c6a6fce3b1c110bf1060ac75c417228a34ab2");
    request.config.params = { nested: [true, null, 0.5], trade_size: 3.0 };
    expect(evolutionRequestDigest(request)).toBe("a0fd1c7682bd74ed9e6a8466342c6a6fce3b1c110bf1060ac75c417228a34ab2");
    request.config.params.trade_size = 4;
    expect(evolutionRequestDigest(request)).not.toBe("a0fd1c7682bd74ed9e6a8466342c6a6fce3b1c110bf1060ac75c417228a34ab2");
    const beforeFunding = evolutionRequestDigest(request);
    request.config.funding_rate = 0.001;
    expect(evolutionRequestDigest(request)).not.toBe(beforeFunding);
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

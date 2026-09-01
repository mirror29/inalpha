import { describe, expect, it, vi } from "vitest";

import { AUTH_SUB_KEY, HookRunner, withHooks } from "../src/hooks/index.js";
import {
  APPROVAL_OPERATION_ID_KEY,
  buildEvolutionLLMSnapshot,
  USER_LLM_SNAPSHOT_KEY,
} from "../src/mastra/llm/evolution-snapshot.js";
import { DEFAULT_PERMISSIONS, PermissionEngine } from "../src/permissions/index.js";
import { PendingApprovalsStore } from "../src/permissions/pending.js";
import { getApprovedEventCampaignContext } from "../src/tools/evolver-shared.js";

const snapshotA = buildEvolutionLLMSnapshot({
  id: "config-a",
  provider: "deepseek",
  model: "deepseek-v4-pro",
  api_key: "not-forwarded-a",
});

const snapshotB = buildEvolutionLLMSnapshot({
  id: "config-b",
  provider: "deepseek",
  model: "deepseek-v4-pro",
  api_key: "not-forwarded-b",
});

const campaignInput = {
  eventSnapshotId: "11111111-1111-4111-8111-111111111111",
  sourceRunId: "22222222-2222-4222-8222-222222222222",
  config: {
    venue: "binance",
    symbol: "BTCUSDT",
    timeframe: "1h" as const,
    from_ts: "2026-08-01T00:00:00Z",
    as_of: "2026-08-02T00:00:00Z",
    initial_cash: 10_000,
    fee_rate: 0.001,
    trading_mode: "perp" as const,
    leverage: 1,
    random_seed: 7,
  },
};

type ToolCtx = { requestContext: Map<string, unknown> };

function context(snapshot = snapshotA): ToolCtx {
  return {
    requestContext: new Map<string, unknown>([[USER_LLM_SNAPSHOT_KEY, snapshot]]),
  };
}

function makeApprovedTool(options?: {
  store?: PendingApprovalsStore;
  owner?: () => string | undefined;
}) {
  const store = options?.store ?? new PendingApprovalsStore();
  const execute = vi.fn(async (_input: unknown, ctx?: unknown) => {
    const requestContext = (ctx as ToolCtx).requestContext;
    return { operationId: requestContext.get(APPROVAL_OPERATION_ID_KEY) };
  });
  const wrapped = withHooks(
    { id: "evolver.run_event_campaign", execute },
    {
      runner: new HookRunner(),
      permissionResolver: () => "ask",
      pendingApprovals: store,
      getAuthSub: options?.owner ?? (() => "user:alice"),
      getSessionId: () => "thread-e2",
    },
  );
  return { store, execute, wrapped };
}

describe("E2 campaign authorization", () => {
  it("requires ask permission instead of the automatic allow path", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    expect(engine.authorize("evolver.run_event_campaign", campaignInput).decision).toBe("ask");
    expect(engine.authorize("evolver.get_event_campaign", {}).decision).toBe("allow");
  });

  it("rejects direct campaign-context construction without a trusted approval operation", async () => {
    const requestContext = new Map<string, unknown>([
      [AUTH_SUB_KEY, "user:alice"],
      [USER_LLM_SNAPSHOT_KEY, snapshotA],
    ]);

    await expect(
      getApprovedEventCampaignContext(campaignInput, requestContext),
    ).rejects.toThrow("explicit event campaign approval context is missing");
  });

  it("fails closed when the frozen LLM approval context is missing", async () => {
    const { store, execute, wrapped } = makeApprovedTool();
    const result = (await wrapped.execute!(campaignInput, {
      requestContext: new Map<string, unknown>(),
    })) as { requiresApproval: boolean; message: string };

    expect(result.requiresApproval).toBe(true);
    expect(result.message).toContain("APPROVAL_UNAVAILABLE");
    expect(execute).not.toHaveBeenCalled();
    expect(store.list("user:alice")).toHaveLength(0);
    store.clearAll();
  });

  it("allows exactly one matching compensation retry for the approved E2 operation", async () => {
    const { store, execute, wrapped } = makeApprovedTool();
    const ctx = context();

    const pending = (await wrapped.execute!(campaignInput, ctx)) as {
      requiresApproval: boolean;
      requestId: string;
    };
    expect(pending.requiresApproval).toBe(true);
    expect(execute).not.toHaveBeenCalled();
    expect(store.respond(pending.requestId, "allow", "user:alice")).toBe(true);

    const first = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    const retry = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    const exhausted = (await wrapped.execute!(campaignInput, ctx)) as {
      requiresApproval: boolean;
      requestId: string;
    };

    expect(first.operationId).toBe(pending.requestId);
    expect(retry.operationId).toBe(pending.requestId);
    expect(exhausted.requiresApproval).toBe(true);
    expect(exhausted.requestId).not.toBe(pending.requestId);
    expect(execute).toHaveBeenCalledTimes(2);
    store.clearAll();
  });

  it("expires the E2 compensation retry after two minutes", async () => {
    vi.useFakeTimers();
    const { store, execute, wrapped } = makeApprovedTool();
    const ctx = context();

    const pending = (await wrapped.execute!(campaignInput, ctx)) as { requestId: string };
    expect(store.respond(pending.requestId, "allow", "user:alice")).toBe(true);
    const first = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    expect(first.operationId).toBe(pending.requestId);

    vi.advanceTimersByTime(2 * 60 * 1_000 + 1);
    const expired = (await wrapped.execute!(campaignInput, ctx)) as {
      requiresApproval: boolean;
      requestId: string;
    };

    expect(expired.requiresApproval).toBe(true);
    expect(expired.requestId).not.toBe(pending.requestId);
    expect(execute).toHaveBeenCalledTimes(1);
    store.clearAll();
    vi.useRealTimers();
  });

  it("does not consume an approval after material campaign input is changed", async () => {
    const { store, execute, wrapped } = makeApprovedTool();
    const ctx = context();

    const pending = (await wrapped.execute!(campaignInput, ctx)) as { requestId: string };
    expect(store.respond(pending.requestId, "allow", "user:alice")).toBe(true);

    const changed = {
      ...campaignInput,
      config: { ...campaignInput.config, symbol: "ETHUSDT" },
    };
    const blocked = (await wrapped.execute!(changed, ctx)) as { requiresApproval: boolean };

    expect(blocked.requiresApproval).toBe(true);
    expect(execute).not.toHaveBeenCalled();

    const original = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    expect(original.operationId).toBe(pending.requestId);
    store.clearAll();
  });

  it("does not consume an approval after the frozen LLM snapshot is substituted", async () => {
    const { store, execute, wrapped } = makeApprovedTool();
    const ctx = context(snapshotA);

    const pending = (await wrapped.execute!(campaignInput, ctx)) as { requestId: string };
    expect(store.respond(pending.requestId, "allow", "user:alice")).toBe(true);

    ctx.requestContext.set(USER_LLM_SNAPSHOT_KEY, snapshotB);
    const blocked = (await wrapped.execute!(campaignInput, ctx)) as {
      requiresApproval: boolean;
    };
    expect(blocked.requiresApproval).toBe(true);
    expect(execute).not.toHaveBeenCalled();

    ctx.requestContext.set(USER_LLM_SNAPSHOT_KEY, snapshotA);
    const original = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    expect(original.operationId).toBe(pending.requestId);
    store.clearAll();
  });

  it("does not let another owner consume the approved operation", async () => {
    let owner = "user:alice";
    const store = new PendingApprovalsStore();
    const { execute, wrapped } = makeApprovedTool({
      store,
      owner: () => owner,
    });
    const ctx = context();

    const pending = (await wrapped.execute!(campaignInput, ctx)) as { requestId: string };
    expect(store.respond(pending.requestId, "allow", "user:alice")).toBe(true);

    owner = "user:bob";
    const blocked = (await wrapped.execute!(campaignInput, ctx)) as {
      requiresApproval: boolean;
    };
    expect(blocked.requiresApproval).toBe(true);
    expect(execute).not.toHaveBeenCalled();

    owner = "user:alice";
    const original = (await wrapped.execute!(campaignInput, ctx)) as { operationId: string };
    expect(original.operationId).toBe(pending.requestId);
    store.clearAll();
  });
});

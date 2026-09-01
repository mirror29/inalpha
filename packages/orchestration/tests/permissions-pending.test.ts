import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";
import { permissionsApiRoutes } from "../src/permissions/api.js";
import {
  approvalInputDigest,
  PendingApprovalsStore,
  pendingApprovals,
} from "../src/permissions/pending.js";

function request(store: PendingApprovalsStore, owner = "user:alice", thread = "thread-A") {
  return store.request({
    authSub: owner,
    sessionId: thread,
    toolName: "paper.promote_candidate",
    toolInput: { candidateId: "c-42", reason: "audit" },
    approvalInput: { candidateId: "c-42" },
    timeoutMs: 5_000,
  });
}

function fakeContext(owner: string | undefined, id: string, decision: "allow" | "deny") {
  const captured = { status: 200, body: null as unknown };
  const requestContext = new Map<string, unknown>();
  if (owner) requestContext.set(AUTH_SUB_KEY, owner);
  return {
    captured,
    ctx: {
      get: (key: string) => (key === "requestContext" ? requestContext : undefined),
      req: {
        param: () => id,
        json: async () => ({ decision }),
        query: () => undefined,
      },
      json: (body: unknown, status = 200) => {
        captured.status = status;
        captured.body = body;
        return { status, body };
      },
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("PendingApprovalsStore", () => {
  it("binds approval to owner, thread, tool and deterministic input digest", async () => {
    const store = new PendingApprovalsStore(() => {});
    const view = request(store);
    expect(view.inputDigest).toBe(approvalInputDigest({ candidateId: "c-42" }));
    expect(store.list("user:bob")).toEqual([]);
    expect(store.list("user:alice")).toHaveLength(1);
    expect(store.respond(view.requestId, "allow", "user:bob")).toBe(false);
    expect(store.respond(view.requestId, "allow", "user:alice")).toBe(true);
    expect(
      await store.consumeApproved({
        authSub: "user:alice",
        sessionId: "thread-B",
        toolName: "paper.promote_candidate",
        approvalInput: { candidateId: "c-42" },
      }),
    ).toBeUndefined();
    expect(
      await store.consumeApproved({
        authSub: "user:alice",
        sessionId: "thread-A",
        toolName: "paper.promote_candidate",
        approvalInput: { candidateId: "c-42" },
      }),
    ).toBe(view.requestId);
    expect(
      await store.consumeApproved({
        authSub: "user:alice",
        sessionId: "thread-A",
        toolName: "paper.promote_candidate",
        approvalInput: { candidateId: "c-42" },
      }),
    ).toBeUndefined();
  });

  it("deny and timeout revoke the record", async () => {
    const store = new PendingApprovalsStore(() => {});
    const denied = request(store);
    expect(store.respond(denied.requestId, "deny", "user:alice")).toBe(true);
    expect(store.size()).toBe(0);

    store.request({
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "risk.update_config",
      toolInput: {},
      approvalInput: {},
      timeoutMs: 20,
    });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(store.size()).toBe(0);
  });

  it("deduplicates repeated pending calls for the same approval identity", () => {
    const store = new PendingApprovalsStore(() => {});
    expect(request(store).requestId).toBe(request(store).requestId);
    expect(store.size()).toBe(1);
    store.clearAll();
  });

  it("reuses one evolution operation ID for a same-scope transport retry", async () => {
    const store = new PendingApprovalsStore(() => {});
    const args = {
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "evolver.run_evolution",
      toolInput: { budget: 1 },
      approvalInput: { request: { budget: 1 }, llm_snapshot: { config_digest: "digest" } },
      timeoutMs: 5_000,
    };
    const view = store.request(args);
    expect(store.respond(view.requestId, "allow", "user:alice")).toBe(true);

    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseAfterConsume: true,
    };
    expect(await store.consumeApproved(consume)).toBe(view.requestId);
    expect(await store.consumeApproved(consume)).toBe(view.requestId);
    expect(
      await store.consumeApproved({
        ...consume,
        approvalInput: { request: { budget: 2 }, llm_snapshot: { config_digest: "digest" } },
      }),
    ).toBeUndefined();
    store.clearAll();
  });

  it("allows one bounded recovery retry and then exhausts it", async () => {
    vi.useFakeTimers();
    const store = new PendingApprovalsStore(() => {});
    const args = {
      authSub: "user:alice",
      sessionId: "thread-E2",
      toolName: "evolver.run_event_campaign",
      toolInput: { eventSnapshotId: "event-1" },
      approvalInput: { request: { eventSnapshotId: "event-1" }, llm_snapshot: { config_digest: "digest" } },
      timeoutMs: 5_000,
    };
    const view = store.request(args);
    expect(store.respond(view.requestId, "allow", args.authSub)).toBe(true);
    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseOnceAfterConsumeMs: 120_000,
    };

    expect(await store.consumeApproved(consume)).toBe(view.requestId);
    expect(await store.consumeApproved(consume)).toBe(view.requestId);
    expect(await store.consumeApproved(consume)).toBeUndefined();
    store.clearAll();
  });

  it("does not allow the bounded recovery retry after two minutes", async () => {
    vi.useFakeTimers();
    const store = new PendingApprovalsStore(() => {});
    const args = {
      authSub: "user:alice",
      sessionId: "thread-E2",
      toolName: "evolver.run_event_campaign",
      toolInput: { eventSnapshotId: "event-1" },
      approvalInput: { request: { eventSnapshotId: "event-1" }, llm_snapshot: { config_digest: "digest" } },
      timeoutMs: 5_000,
    };
    const view = store.request(args);
    expect(store.respond(view.requestId, "allow", args.authSub)).toBe(true);
    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseOnceAfterConsumeMs: 120_000,
    };
    expect(await store.consumeApproved(consume)).toBe(view.requestId);
    vi.advanceTimersByTime(120_001);
    expect(await store.consumeApproved(consume)).toBeUndefined();
    store.clearAll();
  });

  it("serializes concurrent initial E2 consumption into initial plus one retry", async () => {
    let releasePersist!: () => void;
    const persistGate = new Promise<void>((resolve) => {
      releasePersist = resolve;
    });
    const operations = new Map<string, { operationId: string; expiresAt: string }>();
    const persistence = {
      insertPending: vi.fn(async () => {}),
      markResolved: vi.fn(async () => {}),
      rememberEvolutionOperation: vi.fn(async (scope: {
        inputDigest: string;
        operationId: string;
        retentionMs?: number;
      }) => {
        await persistGate;
        const value = {
          operationId: scope.operationId,
          expiresAt: new Date(Date.now() + (scope.retentionMs ?? 86_400_000)).toISOString(),
        };
        operations.set(scope.inputDigest, value);
        return { expiresAt: value.expiresAt };
      }),
      findEvolutionOperation: vi.fn(async (scope: { inputDigest: string }) =>
        operations.get(scope.inputDigest),
      ),
      claimEvolutionOperation: vi.fn(async (scope: { inputDigest: string }) => {
        const value = operations.get(scope.inputDigest);
        if (!value || Date.now() >= Date.parse(value.expiresAt)) return undefined;
        operations.delete(scope.inputDigest);
        return value;
      }),
    };
    const args = {
      authSub: "user:alice",
      sessionId: "thread-E2",
      toolName: "evolver.run_event_campaign",
      toolInput: { eventSnapshotId: "event-1" },
      approvalInput: {
        request: { eventSnapshotId: "event-1" },
        llm_snapshot: { config_digest: "digest" },
      },
      timeoutMs: 5_000,
    };
    const store = new PendingApprovalsStore(() => {}, persistence);
    const view = store.request(args);
    expect(store.respond(view.requestId, "allow", args.authSub)).toBe(true);
    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseOnceAfterConsumeMs: 120_000,
    };

    const initial = store.consumeApproved(consume);
    const concurrent = store.consumeApproved(consume);
    await Promise.resolve();
    expect(persistence.rememberEvolutionOperation).toHaveBeenCalledTimes(1);

    releasePersist();
    expect(await initial).toBe(view.requestId);
    expect(await concurrent).toBe(view.requestId);
    expect(persistence.rememberEvolutionOperation).toHaveBeenCalledTimes(1);
    // Initial consumption probes for a prior durable retry before persisting this approval;
    // the serialized concurrent call performs the second claim and consumes that retry.
    expect(persistence.claimEvolutionOperation).toHaveBeenCalledTimes(2);
    expect(await store.consumeApproved(consume)).toBeUndefined();
    store.clearAll();
  });

  it("atomically allows only one bounded recovery across fresh stores", async () => {
    const operations = new Map<string, { operationId: string; expiresAt: string }>();
    const persistence = {
      insertPending: vi.fn(async () => {}),
      markResolved: vi.fn(async () => {}),
      rememberEvolutionOperation: vi.fn(async (scope: {
        inputDigest: string;
        operationId: string;
        retentionMs?: number;
      }) => {
        const value = {
          operationId: scope.operationId,
          expiresAt: new Date(Date.now() + (scope.retentionMs ?? 86_400_000)).toISOString(),
        };
        operations.set(scope.inputDigest, value);
        return { expiresAt: value.expiresAt };
      }),
      findEvolutionOperation: vi.fn(async (scope: { inputDigest: string }) =>
        operations.get(scope.inputDigest),
      ),
      claimEvolutionOperation: vi.fn(async (scope: { inputDigest: string }) => {
        const value = operations.get(scope.inputDigest);
        if (!value || Date.now() >= Date.parse(value.expiresAt)) return undefined;
        operations.delete(scope.inputDigest);
        return value;
      }),
    };
    const args = {
      authSub: "user:alice",
      sessionId: "thread-E2",
      toolName: "evolver.run_event_campaign",
      toolInput: { eventSnapshotId: "event-1" },
      approvalInput: {
        request: { eventSnapshotId: "event-1" },
        llm_snapshot: { config_digest: "digest" },
      },
      timeoutMs: 5_000,
    };
    const first = new PendingApprovalsStore(() => {}, persistence);
    const view = first.request(args);
    expect(first.respond(view.requestId, "allow", args.authSub)).toBe(true);
    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseOnceAfterConsumeMs: 120_000,
    };
    expect(await first.consumeApproved(consume)).toBe(view.requestId);

    const retryA = new PendingApprovalsStore(() => {}, persistence);
    const retryB = new PendingApprovalsStore(() => {}, persistence);
    const claims = await Promise.all([
      retryA.consumeApproved(consume),
      retryB.consumeApproved(consume),
    ]);

    expect(claims.filter((value) => value === view.requestId)).toHaveLength(1);
    expect(claims.filter((value) => value === undefined)).toHaveLength(1);
    expect(await new PendingApprovalsStore(() => {}, persistence).consumeApproved(consume))
      .toBeUndefined();

    first.clearAll();
    retryA.clearAll();
    retryB.clearAll();
  });

  it("does not reuse a consumed evolution operation after its retention deadline", async () => {
    vi.useFakeTimers();
    const store = new PendingApprovalsStore(() => {});
    const args = {
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "evolver.run_evolution",
      toolInput: { budget: 1 },
      approvalInput: { request: { budget: 1 }, llm_snapshot: { config_digest: "digest" } },
      timeoutMs: 50,
    };
    const view = store.request(args);
    expect(store.respond(view.requestId, "allow", args.authSub)).toBe(true);
    const consume = {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      approvalInput: args.approvalInput,
      reuseAfterConsume: true,
    };
    expect(await store.consumeApproved(consume)).toBe(view.requestId);

    vi.advanceTimersByTime(24 * 60 * 60 * 1_000 + 1);

    expect(await store.consumeApproved(consume)).toBeUndefined();
    store.clearAll();
  });

  it("recovers a durable evolution operation in a fresh store", async () => {
    const operations = new Map<string, { operationId: string; expiresAt: string }>();
    const persistence = {
      insertPending: vi.fn(async () => {}),
      markResolved: vi.fn(async () => {}),
      rememberEvolutionOperation: vi.fn(async (scope: { inputDigest: string; operationId: string }) => {
        const value = {
          operationId: scope.operationId,
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
        };
        operations.set(scope.inputDigest, value);
        return { expiresAt: value.expiresAt };
      }),
      findEvolutionOperation: vi.fn(async (scope: { inputDigest: string }) =>
        operations.get(scope.inputDigest),
      ),
    };
    const args = {
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "evolver.run_evolution",
      toolInput: { budget: 1 },
      approvalInput: { request: { budget: 1 }, llm_snapshot: { config_digest: "digest" } },
      timeoutMs: 5_000,
    };
    const first = new PendingApprovalsStore(() => {}, persistence);
    const view = first.request(args);
    expect(first.respond(view.requestId, "allow", args.authSub)).toBe(true);
    expect(
      await first.consumeApproved({ ...args, reuseAfterConsume: true }),
    ).toBe(view.requestId);

    const recovered = new PendingApprovalsStore(() => {}, persistence);
    expect(
      await recovered.consumeApproved({ ...args, reuseAfterConsume: true }),
    ).toBe(view.requestId);
    first.clearAll();
    recovered.clearAll();
  });
});

describe("permissions approval HTTP API", () => {
  const respondRoute = permissionsApiRoutes.find(
    (route) => route.path === "/permissions/:id/respond" && route.method === "POST",
  )!;

  it("requires an authenticated owner", async () => {
    pendingApprovals.clearAll();
    const view = pendingApprovals.request({
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "paper.promote_candidate",
      toolInput: { candidateId: "c-42" },
      approvalInput: { candidateId: "c-42" },
    });
    const { ctx, captured } = fakeContext(undefined, view.requestId, "allow");
    await respondRoute.handler(ctx as never, async () => {});
    expect(captured.status).toBe(401);
    pendingApprovals.clearAll();
  });

  it("writes explicit approve only for the matching owner", async () => {
    pendingApprovals.clearAll();
    const view = pendingApprovals.request({
      authSub: "user:alice",
      sessionId: "thread-A",
      toolName: "paper.promote_candidate",
      toolInput: { candidateId: "c-42" },
      approvalInput: { candidateId: "c-42" },
    });
    const wrong = fakeContext("user:bob", view.requestId, "allow");
    await respondRoute.handler(wrong.ctx as never, async () => {});
    expect(wrong.captured.status).toBe(404);

    const matching = fakeContext("user:alice", view.requestId, "allow");
    await respondRoute.handler(matching.ctx as never, async () => {});
    expect(matching.captured.status).toBe(200);
    expect(
      await pendingApprovals.consumeApproved({
        authSub: "user:alice",
        sessionId: "thread-A",
        toolName: "paper.promote_candidate",
        approvalInput: { candidateId: "c-42" },
      }),
    ).toBe(view.requestId);
  });
});

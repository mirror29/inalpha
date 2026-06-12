/**
 * Scheduler 单元测试 —— 覆盖 runJob 的 tool / agent 双模式 + 失败路径 + overlap 防护。
 *
 * mock 策略：
 *
 * - repo.ts：vi.mock 替换 insertRun / completeRun / hasRunningRun，避免连 Postgres
 * - wired-tools.ts：vi.mock 注入 fake tool 数组，断言 ctx 收到 authToken
 * - mastra：fake 对象，agent.generate 是 vi.fn()
 *
 * 不测：
 *
 * - 真 Postgres CRUD（需要 docker-compose 起库；归到 e2e smoke）
 * - croner 触发节拍（归到 scheduler/index.test，本文件只测 runner）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";

vi.mock("../src/scheduler/repo.js", () => {
  const insertRun = vi.fn(async () => "run-uuid-1");
  const completeRun = vi.fn(async () => undefined);
  const hasRunningRun = vi.fn(async () => false);
  return { insertRun, completeRun, hasRunningRun };
});

// issue #65：Stop hook 的生产 fetcher 打 paper HTTP——测试里换成可控内存版。
// pendingPlans 当前内容 = fetcher 每次返回的"未执行 plan"列表。
const pendingPlans = vi.hoisted(() => ({
  plans: [] as Array<{ plan_id: string; status: string; symbol: string }>,
}));
vi.mock("../src/hooks/handlers/pending-plan-fetcher.js", () => ({
  createPaperPendingPlanFetcher: () => async () => pendingPlans.plans,
}));

import { completeRun, hasRunningRun, insertRun } from "../src/scheduler/repo.js";
import { runJob } from "../src/scheduler/runner.js";
import type { ScheduledJob } from "../src/scheduler/types.js";

/**
 * 构造一个 fake mastra：getAgent('orchestrator') 返回一个含 tools Record + generate 的对象。
 * runner.runToolMode 走 tools[name].execute；runAgentMode 走 agent.generate。
 */
function buildFakeMastra(opts: {
  toolFn?: ReturnType<typeof vi.fn>;
  boomFn?: ReturnType<typeof vi.fn>;
  generateFn?: ReturnType<typeof vi.fn>;
  noAgent?: boolean;
} = {}) {
  const fakeToolExec =
    opts.toolFn ??
    vi.fn(async (input: unknown, ctx: { requestContext: { authToken: string } }) => ({
      ok: true,
      input,
      sawToken: ctx.requestContext.authToken.startsWith("eyJ") ? "JWT" : "raw",
    }));
  const boomExec =
    opts.boomFn ??
    vi.fn(async () => {
      throw new Error("boom");
    });
  const generate =
    opts.generateFn ??
    vi.fn(async () => ({
      text: "ok",
      usage: null,
      finishReason: "stop",
    }));
  const agent = {
    generate,
    tools: {
      "data.backfill_bars": { id: "data.backfill_bars", execute: fakeToolExec },
      "paper.boom": { id: "paper.boom", execute: boomExec },
    },
  };
  return {
    fakeToolExec,
    boomExec,
    generate,
    mastra: {
      getAgent: vi.fn((id: string) => (opts.noAgent ? undefined : id === "orchestrator" ? agent : undefined)),
    } as never,
  };
}

const TEST_SECRET = "test-secret-32-chars-or-more-xxxxxxx";

const baseJobFields = {
  enabled: true,
  description: null,
  createdAt: new Date(),
  updatedAt: new Date(),
};

function buildToolJob(overrides: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    ...baseJobFields,
    jobId: "hourly_btc_backfill",
    cronExpr: "5 * * * *",
    timezone: "UTC",
    mode: "tool",
    payload: {
      tool: "data.backfill_bars",
      input: { venue: "binance", symbol: "BTC/USDT", timeframe: "1h" },
    },
    ...overrides,
  } as ScheduledJob;
}

function buildAgentJob(): ScheduledJob {
  return {
    ...baseJobFields,
    jobId: "daily_btc_deep_dive",
    cronExpr: "0 8 * * *",
    timezone: "Asia/Shanghai",
    mode: "agent",
    payload: { agent: "orchestrator", prompt: "对 BTC 做 deep_dive" },
  } as ScheduledJob;
}

beforeEach(() => {
  setSettings({
    dataServiceUrl: "http://data-mock.test",
    paperServiceUrl: "http://paper-mock.test",
    researchServiceUrl: "http://research-mock.test",
    jwtSecret: TEST_SECRET,
    jwtAlgorithm: "HS256",
    schedulerEnabled: false,
    databaseUrl: undefined,
    // agent mode 起跑要把 working memory scope 钉到控制台账户（resourceId）——
    // 生产由 zod 默认 "console:dev" 兜底，测试显式注入对齐。
    consoleSubject: "console:dev",
  });
  vi.mocked(insertRun).mockClear();
  vi.mocked(completeRun).mockClear();
  vi.mocked(hasRunningRun).mockClear();
  vi.mocked(hasRunningRun).mockResolvedValue(false);
  pendingPlans.plans = [];
});

afterEach(() => {
  clearSettings();
});

describe("runJob · tool mode", () => {
  it("从 mastra.getAgent('orchestrator').tools 找 tool 后调 execute 并写 success", async () => {
    const { mastra, fakeToolExec } = buildFakeMastra();
    const job = buildToolJob();
    const result = await runJob({
      job,
      mastra,
      scheduledAt: new Date("2026-05-24T01:05:00Z"),
      trigger: "cron",
    });

    expect(result.status).toBe("success");
    expect(result.runId).toBe("run-uuid-1");
    expect(insertRun).toHaveBeenCalledWith({
      jobId: job.jobId,
      scheduledAt: expect.any(Date),
      trigger: "cron",
    });
    expect(completeRun).toHaveBeenCalledWith(
      "run-uuid-1",
      expect.objectContaining({ status: "success" }),
    );
    // tool execute 收到的 ctx 含 JWT 风格的 authToken
    expect(fakeToolExec).toHaveBeenCalledTimes(1);
    const ctx = fakeToolExec.mock.calls[0]![1] as { requestContext: { authToken: string } };
    expect(ctx.requestContext.authToken).toMatch(/^eyJ/);
  });

  it("tool 不存在时返回 failed + error.code 提示", async () => {
    const { mastra } = buildFakeMastra();
    const job = buildToolJob({
      payload: { tool: "unknown.tool", input: {} },
    } as Partial<ScheduledJob>);

    const result = await runJob({
      job,
      mastra,
      scheduledAt: new Date(),
      trigger: "manual",
    });

    expect(result.status).toBe("failed");
    expect(completeRun).toHaveBeenCalledWith(
      "run-uuid-1",
      expect.objectContaining({
        status: "failed",
        error: expect.objectContaining({
          message: expect.stringContaining("unknown.tool"),
        }),
      }),
    );
  });

  it("tool execute 抛错时写 failed 并不抛", async () => {
    const { mastra } = buildFakeMastra();
    const job = buildToolJob({
      payload: { tool: "paper.boom", input: {} },
    } as Partial<ScheduledJob>);

    const result = await runJob({
      job,
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });

    expect(result.status).toBe("failed");
    expect((result.error as Record<string, unknown>).message).toBe("boom");
  });
});

describe("runJob · agent mode", () => {
  it("调 mastra.getAgent('orchestrator').generate(prompt) 并写 success", async () => {
    const generate = vi.fn(async () => ({
      text: "deep_dive 完成",
      usage: { input_tokens: 100, output_tokens: 50 },
      finishReason: "stop",
    }));
    const { mastra } = buildFakeMastra({ generateFn: generate });

    const result = await runJob({
      job: buildAgentJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "manual",
    });

    expect(result.status).toBe("success");
    expect(generate).toHaveBeenCalledTimes(1);
    // issue #65 起 prompt 以 messages 数组传入（force-continue 轮次要携带上下文）
    const [messages, options] = generate.mock.calls[0]!;
    expect(JSON.stringify(messages)).toContain("BTC");
    // options.requestContext 应该是个 RequestContext 实例（class，有 get 方法）
    expect(options).toMatchObject({
      requestContext: expect.anything(),
      abortSignal: expect.any(AbortSignal),
    });
  });

  it("agent 未注册时返回 failed", async () => {
    const { mastra } = buildFakeMastra({ noAgent: true });
    const result = await runJob({
      job: buildAgentJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });
    expect(result.status).toBe("failed");
    expect((result.error as Record<string, unknown>).message).toContain("orchestrator");
  });
});

describe("runJob · agent mode Stop hook（issue #65）", () => {
  it("有残留 plan → 注入 [system_notice] 强制再 generate；干完后正常结束", async () => {
    pendingPlans.plans = [
      { plan_id: "p1", status: "approved", symbol: "BTC/USDT" },
    ];
    const generate = vi.fn(
      async (messages: Array<{ role: string; content: string }>) => {
        // 第二轮收到 system_notice 后模拟 LLM 把 plan 执行掉
        if (messages.some((m) => m.content.includes("[system_notice]"))) {
          pendingPlans.plans = [];
        }
        return { text: "done", usage: null, finishReason: "stop" };
      },
    );
    const { mastra } = buildFakeMastra({ generateFn: generate as never });

    const result = await runJob({
      job: buildAgentJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });

    expect(result.status).toBe("success");
    expect(generate).toHaveBeenCalledTimes(2);
    // 第二轮 messages 应携带前轮上下文 + 残留提醒（含 plan id）
    const secondMessages = generate.mock.calls[1]![0];
    const flat = JSON.stringify(secondMessages);
    expect(flat).toContain("[system_notice]");
    expect(flat).toContain("p1");
    expect(flat).toContain("BTC"); // 原 prompt 仍在上下文里
    expect(
      (result.result as { stop_hook_force_count: number }).stop_hook_force_count,
    ).toBe(1);
  });

  it("残留一直不清 → 最多强制 3 次（StopHookRunner 限流）后放行结束", async () => {
    pendingPlans.plans = [
      { plan_id: "p1", status: "pending_approval", symbol: "BTC/USDT" },
    ];
    const generate = vi.fn(async () => ({
      text: "still ignoring",
      usage: null,
      finishReason: "stop",
    }));
    const { mastra } = buildFakeMastra({ generateFn: generate });

    const result = await runJob({
      job: buildAgentJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });

    expect(result.status).toBe("success");
    expect(generate).toHaveBeenCalledTimes(4); // 首轮 + 3 次强制
    expect(
      (result.result as { stop_hook_force_count: number }).stop_hook_force_count,
    ).toBe(3);
  });

  it("无残留 plan → 单轮结束，不注入任何 notice", async () => {
    const generate = vi.fn(async () => ({
      text: "ok",
      usage: null,
      finishReason: "stop",
    }));
    const { mastra } = buildFakeMastra({ generateFn: generate });

    const result = await runJob({
      job: buildAgentJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });

    expect(result.status).toBe("success");
    expect(generate).toHaveBeenCalledTimes(1);
    expect(
      (result.result as { stop_hook_force_count: number }).stop_hook_force_count,
    ).toBe(0);
  });
});

describe("runJob · overlap 防护", () => {
  it("hasRunningRun 返回 true 时跳过本次触发且不 insert", async () => {
    vi.mocked(hasRunningRun).mockResolvedValueOnce(true);
    const { mastra } = buildFakeMastra();
    const result = await runJob({
      job: buildToolJob(),
      mastra,
      scheduledAt: new Date(),
      trigger: "cron",
    });
    expect(result.status).toBe("failed");
    expect((result.error as Record<string, unknown>).code).toBe("OVERLAP_PREVENTED");
    expect(insertRun).not.toHaveBeenCalled();
    expect(completeRun).not.toHaveBeenCalled();
  });
});

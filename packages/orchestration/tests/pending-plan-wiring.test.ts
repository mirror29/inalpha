/**
 * issue #65 —— pending-plan-check 生产接线的两个新件：
 *
 * 1. ``createPendingPlanNoticeProcessor``（chat 路径）：turn 内调过 trade.create_plan /
 *    approve_plan 且仍有未执行 plan → 把 [system_notice] 追加到最终 assistant 消息
 * 2. ``createPaperPendingPlanFetcher``（生产 fetcher）：并发拉 pending_approval +
 *    approved 两状态并合并映射成 PendingPlanLite
 *
 * StopHookRunner / handler 本体的行为见 ``stop-hooks.test.ts``；scheduler 强制续 turn
 * 的集成见 ``scheduler.test.ts``。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { createPendingPlanNoticeProcessor } from "../src/hooks/processors/pending-plan-notice.js";
import type { PendingPlanLite } from "../src/hooks/handlers/pending-plan-check.js";

// ─── fetcher 的 PaperClient 替身 ────────────────────────────────────
const listPlansMock = vi.hoisted(() =>
  vi.fn(async ({ status }: { status: string }) =>
    status === "approved"
      ? [
          {
            plan_id: "ap-1",
            status: "approved",
            symbol: "BTC/USDT",
            created_at: "2026-06-11T00:00:00Z",
          },
        ]
      : [
          {
            plan_id: "pe-1",
            status: "pending_approval",
            symbol: "ETH/USDT",
            created_at: "2026-06-11T01:00:00Z",
          },
        ],
  ),
);
vi.mock("../src/clients/paper.js", () => ({
  PaperClient: class {
    listPlans = listPlansMock;
  },
}));

import { createPaperPendingPlanFetcher } from "../src/hooks/handlers/pending-plan-fetcher.js";

// ─── processor 测试用的消息 / 结果脚手架 ────────────────────────────

type LooseMessage = {
  role: string;
  content: { format: 2; parts: Array<{ type: string; text?: string }> };
};

function makeMessages(): LooseMessage[] {
  return [
    {
      role: "assistant",
      content: { format: 2, parts: [{ type: "text", text: "计划已创建" }] },
    },
  ];
}

function makeResult(toolNames: string[]) {
  return {
    text: "计划已创建",
    usage: {},
    finishReason: "stop",
    steps: [{ toolCalls: toolNames.map((toolName) => ({ toolName })) }],
  };
}

const PLANS: PendingPlanLite[] = [
  { plan_id: "p1", status: "approved", symbol: "BTC/USDT" },
];

async function runProcessor(opts: {
  fetcher?: () => Promise<PendingPlanLite[]>;
  toolNames: string[];
  messages?: LooseMessage[];
}) {
  const processor = createPendingPlanNoticeProcessor({
    fetcher: opts.fetcher as never,
  });
  const messages = opts.messages ?? makeMessages();
  const out = await processor.processOutputResult!({
    messages: messages as never,
    result: makeResult(opts.toolNames) as never,
    state: {},
    abort: (() => {
      throw new Error("abort called");
    }) as never,
    messageList: undefined as never,
    retryCount: 0,
  } as never);
  return { messages, out };
}

describe("pending-plan-notice processor（chat 路径）", () => {
  it("调过 trade.create_plan 且有残留 → 最终 assistant 消息追加 [system_notice]", async () => {
    const fetcher = vi.fn(async () => PLANS);
    const { messages } = await runProcessor({
      fetcher,
      toolNames: ["data.get_bars", "trade.create_plan"],
    });
    const parts = messages[0]!.content.parts;
    expect(parts.length).toBe(2);
    expect(parts[1]!.text).toContain("[system_notice]");
    expect(parts[1]!.text).toContain("p1");
  });

  it("本 turn 没碰 trade plan tool → 不调 fetcher、消息不变", async () => {
    const fetcher = vi.fn(async () => PLANS);
    const { messages } = await runProcessor({
      fetcher,
      toolNames: ["data.get_bars", "web.search"],
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(messages[0]!.content.parts.length).toBe(1);
  });

  it("fetcher 返回空（plan 都执行完了）→ 消息不变", async () => {
    const { messages } = await runProcessor({
      fetcher: async () => [],
      toolNames: ["trade.create_plan"],
    });
    expect(messages[0]!.content.parts.length).toBe(1);
  });

  it("fetcher 抛错 → 静默放过，不阻断回复", async () => {
    const { messages } = await runProcessor({
      fetcher: async () => {
        throw new Error("paper down");
      },
      toolNames: ["trade.approve_plan"],
    });
    expect(messages[0]!.content.parts.length).toBe(1);
  });

  it("不注入 fetcher → noop（dev / 测试友好，与 handler 行为一致）", async () => {
    const { messages } = await runProcessor({
      toolNames: ["trade.create_plan"],
    });
    expect(messages[0]!.content.parts.length).toBe(1);
  });
});

describe("createPaperPendingPlanFetcher（生产 fetcher）", () => {
  beforeEach(() => {
    setSettings({
      dataServiceUrl: "http://data-mock.test",
      paperServiceUrl: "http://paper-mock.test",
      researchServiceUrl: "http://research-mock.test",
      jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
      jwtAlgorithm: "HS256",
      schedulerEnabled: false,
      databaseUrl: undefined,
    });
    listPlansMock.mockClear();
  });

  afterEach(() => {
    clearSettings();
  });

  it("并发拉 pending_approval + approved 两状态并合并映射", async () => {
    const fetcher = createPaperPendingPlanFetcher();
    const plans = await fetcher(undefined);

    expect(listPlansMock).toHaveBeenCalledTimes(2);
    const statuses = listPlansMock.mock.calls.map((c) => c[0]!.status).sort();
    expect(statuses).toEqual(["approved", "pending_approval"]);

    expect(plans).toHaveLength(2);
    const ids = plans.map((p) => p.plan_id).sort();
    expect(ids).toEqual(["ap-1", "pe-1"]);
    // 映射保真：status / symbol / created_at 透传
    const approved = plans.find((p) => p.plan_id === "ap-1")!;
    expect(approved.status).toBe("approved");
    expect(approved.symbol).toBe("BTC/USDT");
    expect(approved.created_at).toBe("2026-06-11T00:00:00Z");
  });
});

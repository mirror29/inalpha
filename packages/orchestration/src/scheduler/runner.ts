/**
 * Scheduler runner —— 真正执行一个 job 的入口。
 *
 * 职责：
 *
 * 1. 在 `scheduler_runs` 插一行 `status='running'`
 * 2. 自签 service token（sub 带 job_id，便于 audit log 区分）
 * 3. 根据 mode 派发：
 *    - `tool` —— 在 wiredOrchestratorTools 里按 id 找 tool，套 plain ctx 调 execute
 *    - `agent` —— 调 mastra.getAgent('orchestrator').generate(prompt)，5min hard timeout
 * 4. 回写 finished_at / status / result / error
 *
 * 何时用：仅 scheduler/index.ts 内部 + scripts/scheduler-trigger.ts。
 *
 * 何时不用：业务代码不要 import 这个文件——cron 触发的语义只有调度层关心。
 *
 * 坑：
 *
 * - agent mode 内部 tool 调用走 mastra runtime，token 通过 RequestContext 注入
 *   但 Inalpha 现有 tool 解构 `ctx?.requestContext.authToken` 为 plain object 字段，
 *   class 实例的 `MASTRA_AUTH_TOKEN_KEY` 不一定能拿到——此时 tool 内自带的
 *   `mintServiceToken({ sub: "service:orchestration" })` 兜底会启动，
 *   audit log 的 sub 会是 service:orchestration 而非 service:scheduler:<job>。
 *   这是 D-9 MVP 的已知限制，未来改 tool 解构方式即可修复。
 * - permissions deny 在两种 mode 下都生效（wired tool 走完整链路），
 *   不存在"cron 绕过 plan/exec deny"风险。
 */
import type { Mastra } from "@mastra/core/mastra";
import { RequestContext } from "@mastra/core/request-context";

import { mintServiceToken } from "../auth.js";
import { completeRun, hasRunningRun, insertRun } from "./repo.js";
import type {
  AgentJobPayload,
  RunTrigger,
  ScheduledJob,
  ToolJobPayload,
} from "./types.js";

/** agent mode 单次 generate 上限 5 分钟。 */
const AGENT_HARD_TIMEOUT_MS = 5 * 60 * 1000;

export interface RunJobArgs {
  job: ScheduledJob;
  mastra: Mastra;
  scheduledAt: Date;
  trigger: RunTrigger;
}

export interface RunJobResult {
  runId: string;
  status: "success" | "failed" | "timeout";
  result?: unknown;
  error?: unknown;
}

/**
 * 执行一个 job —— Scheduler / CLI / HTTP 手动触发的统一入口。
 *
 * 默认不抛错——失败也写表，便于审计与展示。
 */
export async function runJob(args: RunJobArgs): Promise<RunJobResult> {
  const { job, mastra, scheduledAt, trigger } = args;

  // 双保险防 overlap：除了 croner 内置 protection，再查一次 DB
  if (await hasRunningRun(job.jobId)) {
    return {
      runId: "",
      status: "failed",
      error: {
        code: "OVERLAP_PREVENTED",
        message: `job ${job.jobId} 还有 running 行，跳过本次触发`,
      },
    };
  }

  const runId = await insertRun({ jobId: job.jobId, scheduledAt, trigger });
  const token = await mintServiceToken(
    { sub: `service:scheduler:${job.jobId}` },
    3600,
  );

  try {
    let result: unknown;
    if (job.mode === "tool") {
      result = await runToolMode(job.payload, token, mastra);
    } else {
      result = await runAgentMode(job.payload, token, mastra);
    }
    await completeRun(runId, { status: "success", result });
    return { runId, status: "success", result };
  } catch (err) {
    const isTimeout =
      err instanceof Error &&
      (err.name === "AbortError" || err.message.includes("aborted"));
    const status: "failed" | "timeout" = isTimeout ? "timeout" : "failed";
    const errorPayload = serializeError(err);
    await completeRun(runId, { status, error: errorPayload });
    return { runId, status, error: errorPayload };
  }
}

// ============ tool mode ============

/**
 * 通过 mastra.getAgent('orchestrator').tools 动态拿 wired tool 调度。
 *
 * 为什么不 import wiredOrchestratorTools：避免 ESM 循环
 * （runner ← wired-tools ← tools/index ← tools/scheduler ← runner）。
 * agent.tools 是 Mastra 创建 Agent 时已经 freeze 的 Record，runtime 拿稳定。
 */
async function runToolMode(
  payload: ToolJobPayload,
  token: string,
  mastra: Mastra,
): Promise<unknown> {
  const agent = mastra.getAgent("orchestrator");
  if (agent === undefined) {
    throw new Error("scheduler.runToolMode: orchestrator agent 未注册");
  }
  const tools = (agent as unknown as { tools?: Record<string, { execute?: Function }> })
    .tools;
  const tool = tools?.[payload.tool];
  if (tool === undefined || typeof tool.execute !== "function") {
    throw new Error(
      `scheduler.runToolMode: tool ${payload.tool} 未在 orchestrator.tools 注册`,
    );
  }
  const ctx = { requestContext: { authToken: token }, mastra } as never;
  return await tool.execute(payload.input, ctx);
}

// ============ agent mode ============

async function runAgentMode(
  payload: AgentJobPayload,
  token: string,
  mastra: Mastra,
): Promise<unknown> {
  const agent = mastra.getAgent(payload.agent);
  if (agent === undefined) {
    throw new Error(`scheduler.runAgentMode: agent ${payload.agent} 未注册`);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AGENT_HARD_TIMEOUT_MS);

  try {
    const rc = new RequestContext([["authToken", token]]);
    // Mastra agent.generate 在 1.36 支持 abortSignal + requestContext
    const out = (await agent.generate(payload.prompt, {
      requestContext: rc,
      abortSignal: controller.signal,
    } as never)) as { text?: string; usage?: unknown; finishReason?: string };

    return {
      text: out.text ?? null,
      usage: out.usage ?? null,
      finishReason: out.finishReason ?? null,
    };
  } finally {
    clearTimeout(timer);
  }
}

// ============ 错误序列化 ============

function serializeError(err: unknown): Record<string, unknown> {
  if (err instanceof Error) {
    return {
      code: (err as { code?: string }).code ?? err.name,
      message: err.message,
      stack_excerpt: (err.stack ?? "").split("\n").slice(0, 8).join("\n"),
    };
  }
  return { code: "UNKNOWN", message: String(err) };
}

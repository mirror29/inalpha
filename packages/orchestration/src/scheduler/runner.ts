/**
 * Scheduler runner —— 真正执行一个 job 的入口。
 *
 * 职责：
 *
 * 1. 在 `scheduler_runs` 插一行 `status='running'`
 * 2. 自签 service token（sub=defaultServiceSubject()，与控制台同账户——cron 跑出来的
 *    run/候选/回测控制台才看得到；job_id 作为额外 claim 保留便于 audit 区分）
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
 *   `mintServiceToken({ sub: defaultServiceSubject() })` 兜底会启动，落到的还是
 *   控制台账户（与本 runner 一致），故 cron 产物对控制台始终可见。
 *   这是 D-9 MVP 的已知限制，未来改 tool 解构方式即可让调用者身份精确透传。
 * - permissions deny 在两种 mode 下都生效（wired tool 走完整链路），
 *   不存在"cron 绕过 plan/exec deny"风险。
 */
import type { Mastra } from "@mastra/core/mastra";
import { RequestContext } from "@mastra/core/request-context";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import {
  HookRunner,
  StopHookRunner,
  createPaperPendingPlanFetcher,
  createPendingPlanCheckHandler,
  formatStopNotice,
} from "../hooks/index.js";
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
  /** 本次触发创建的 run id；`null` = 没创建 run（如 overlap 跳过,无对应 runs 行）。 */
  runId: string | null;
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
      runId: null, // overlap 跳过,没创建 run —— 不用 "" 假 id 冒充
      status: "failed",
      error: {
        code: "OVERLAP_PREVENTED",
        message: `job ${job.jobId} 还有 running 行，跳过本次触发`,
      },
    };
  }

  const runId = await insertRun({ jobId: job.jobId, scheduledAt, trigger });
  // sub 用 defaultServiceSubject()（=控制台账户）让 cron 产物落到用户可见的账户;
  // job_id 作为额外 claim 保留,审计需要时仍可区分是哪个 job 跑的(account 只看 sub)。
  const token = await mintServiceToken(
    { sub: defaultServiceSubject(), scheduler_job: job.jobId },
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

/**
 * agent mode 的 Stop hook 调度器（issue #65 / ADR-0010 §Stop hook）。
 *
 * cron 跑 agent 是**无人值守**场景——LLM 调完 trade.create_plan 就结束 turn 的话，
 * pending plan 会静默残留到没人看的 scheduler_runs 里。这里我们自己持有 generate
 * 循环，可以真正实现"Stop hook 强制再 turn"：每次 generate 结束跑一遍 Stop hook，
 * 有残留 plan 就把 [system_notice] 当下一条 user 消息续跑（StopHookRunner 限流 ≤3 次）。
 */
function buildStopRunner(token: string): StopHookRunner {
  const hookRunner = new HookRunner();
  hookRunner.register({
    id: "pending-plan-check",
    event: "Stop",
    handler: createPendingPlanCheckHandler({
      fetcher: createPaperPendingPlanFetcher({ token }),
    }),
    // 护栏失败（paper 抖动）不该让 cron run 整体 failed —— handler 自身已 fail-safe，
    // blocking=false 再兜一层 runner 级异常
    blocking: false,
  });
  return new StopHookRunner(hookRunner);
}

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
  const stopRunner = buildStopRunner(token);

  try {
    const rc = new RequestContext([["authToken", token]]);
    // 累积消息数组而非裸 prompt：force-continue 的第二轮 generate 要带上前轮上下文，
    // 否则 LLM 只看到一句 [system_notice]，不知道 plan 是怎么来的
    const messages: Array<{ role: "user" | "assistant"; content: string }> = [
      { role: "user", content: payload.prompt },
    ];
    let out: { text?: string; usage?: unknown; finishReason?: string };
    let forceCount = 0;
    for (;;) {
      // Mastra agent.generate 在 1.36 支持 abortSignal + requestContext
      out = (await agent.generate(messages as never, {
        requestContext: rc,
        abortSignal: controller.signal,
      } as never)) as { text?: string; usage?: unknown; finishReason?: string };

      const decision = await stopRunner.maybeForceContinue({
        sessionId: `scheduler:${payload.agent}`,
        metadata: { agent: payload.agent },
      });
      if (!decision.shouldContinue) break;
      forceCount = decision.forceCount;
      messages.push({ role: "assistant", content: out.text ?? "" });
      messages.push({
        role: "user",
        content: formatStopNotice(decision.reason ?? "unfinished work detected"),
      });
    }

    return {
      text: out.text ?? null,
      usage: out.usage ?? null,
      finishReason: out.finishReason ?? null,
      // 审计留痕：本次 run 被 Stop hook 强制续了几轮（0 = 一把过）
      stop_hook_force_count: forceCount,
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

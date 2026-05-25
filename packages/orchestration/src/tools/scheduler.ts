/**
 * services/scheduler 的 Mastra tool 包装（D-9 · 类 Hermes 定时 agent 管理）。
 *
 * 让 orchestrator agent 能在对话里管理定时任务：查列表、切 enabled、立即触发、看历史。
 * 这是"用 agent 测试 scheduler"的入口。
 *
 * Tool 设计遵循 docs/05-tool-skill-discipline.md 的"做什么 / 何时用 / 何时不用 / 坑"四要素。
 */
import type { Mastra } from "@mastra/core/mastra";
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import * as repo from "../scheduler/repo.js";
import { runJob } from "../scheduler/runner.js";

const JobIdSchema = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9_]+$/, "job_id 只允许 a-z / 0-9 / _");

const CronExprSchema = z
  .string()
  .min(9)
  .max(120)
  .describe(
    "标准 5 字段 cron 表达式：'分 时 日 月 周'。示例：" +
      "'*/5 * * * *' 每 5 分钟 / '0 * * * *' 每小时整点 / " +
      "'0 8 * * *' 每天 08:00 / '5 0 * * 1' 每周一 00:05",
  );

const TimezoneSchema = z
  .string()
  .min(1)
  .default("UTC")
  .describe("IANA tz database 名，如 'UTC' / 'Asia/Shanghai' / 'America/New_York'");

// ────────────────────────────────────────────────────────────────────
// scheduler.create_job
// ────────────────────────────────────────────────────────────────────

export const schedulerCreateJobTool = createTool({
  id: "scheduler.create_job",
  description: `
    新建一个定时任务并写入 DB。enabled=true 时立刻进入 cron 排程（最多 60s 后被 leader 进程发现）。

    两种 mode：

    - **mode='tool'**：cron 到点直接调一个 tool，参数固定（不走 LLM）。
      适合"周期性拉行情 / 周期性查健康"这类零智能动作。
      payload = { tool: 'data.get_bars', input: { symbol, timeframe, limit } }
      payload.tool 必须是 orchestrator 已挂的 tool id（如 data.get_bars / data.backfill_bars /
      paper.list_strategies / paper.health；**不能**是 paper.submit_order_intent，permissions deny）。

    - **mode='agent'**：cron 到点跑一遍 orchestrator agent + 给定 prompt。
      适合"每日盘前研究 / 周复盘"这类需要 LLM 编排多 tool 的动作。
      payload = { prompt: '<给 LLM 的指令>' }

    何时用：
    - 用户说"每 X 分钟跑一下 Y"/"每天 X 点做 Y"/"定时拉 Z"/"创建一个 schedule"
    - 想固化一个对话里手动重复的动作

    何时不用：
    - 用户只想现在跑一次 → 直接用对应 tool，不要绕 scheduler
    - 用户期望"主动推送到对话窗口" → **当前不支持**！cron 跑出的 result 只落 scheduler_runs 表，
      不会发回 playground 对话。明确告诉用户：建好之后他想看结果要 "查一下 X 最近跑的结果"
      （触发 scheduler.list_runs），或开 admin 页 (scripts/scheduler-admin.html) 看。

    坑：
    - cron 表达式是标准 5 字段格式 '分 时 日 月 周'。**不要写 6/7 字段的扩展格式**
    - timezone 默认 UTC；crypto 24h 没盘前盘后，但用户可能习惯本地时间，按需指定 Asia/Shanghai
    - jobId 必须 a-z / 0-9 / _，且全局唯一；冲突会报错
    - SCHEDULER_ENABLED=false 时本进程不跑 cron——job 建好了但不会自动触发；
      要么开 SCHEDULER_ENABLED 重启，要么用 scheduler.trigger_job 手动测一次
  `.trim(),
  // 顶层必须是 z.object（DeepSeek / OpenAI function calling 拒 anyOf 顶层 schema）。
  // mode 是 enum，tool/input/prompt 都 optional，运行时按 mode 校验。
  inputSchema: z.object({
    jobId: JobIdSchema,
    cronExpr: CronExprSchema,
    timezone: TimezoneSchema,
    enabled: z.boolean().default(true),
    description: z.string().max(200).optional(),
    mode: z.enum(["tool", "agent"]),
    tool: z
      .string()
      .min(1)
      .optional()
      .describe("仅 mode='tool' 必填。orchestrator 已挂的 tool id，如 'data.get_bars'"),
    input: z
      .unknown()
      .optional()
      .describe("仅 mode='tool'。透传给 tool.execute 的输入（按目标 tool 的 inputSchema 填）"),
    prompt: z
      .string()
      .min(1)
      .max(2000)
      .optional()
      .describe("仅 mode='agent' 必填。给 orchestrator agent 的指令，cron 到点会执行一遍"),
  }),
  execute: async (input) => {
    if (input.mode === "tool" && (input.tool === undefined || input.tool.length === 0)) {
      return { ok: false, error: "mode='tool' 时 tool 必填" };
    }
    if (input.mode === "agent" && (input.prompt === undefined || input.prompt.length === 0)) {
      return { ok: false, error: "mode='agent' 时 prompt 必填" };
    }
    try {
      const created =
        input.mode === "tool"
          ? await repo.createJob({
              jobId: input.jobId,
              cronExpr: input.cronExpr,
              timezone: input.timezone,
              enabled: input.enabled,
              description: input.description ?? null,
              mode: "tool",
              payload: { tool: input.tool!, input: input.input ?? {} },
            })
          : await repo.createJob({
              jobId: input.jobId,
              cronExpr: input.cronExpr,
              timezone: input.timezone,
              enabled: input.enabled,
              description: input.description ?? null,
              mode: "agent",
              payload: { agent: "orchestrator", prompt: input.prompt! },
            });
      return {
        ok: true,
        jobId: created.jobId,
        cronExpr: created.cronExpr,
        timezone: created.timezone,
        mode: created.mode,
        enabled: created.enabled,
        notice:
          "已创建。提醒：cron 触发的结果落 scheduler_runs 表，不会主动推送到当前对话；" +
          "想看就让我 list_runs 查，或开 scripts/scheduler-admin.html admin 页。",
      };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
        hint:
          "常见原因：jobId 已存在（换名）/ cron 格式错（标准 5 字段）/ DB 没连上。",
      };
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// scheduler.list_jobs
// ────────────────────────────────────────────────────────────────────

export const schedulerListJobsTool = createTool({
  id: "scheduler.list_jobs",
  description: `
    列出全部 scheduler 任务（含 disabled），返回 job_id / cron / timezone / mode / enabled / description。

    何时用：
    - 用户问"有哪些定时任务"/"scheduler 跑什么"/"哪些 job"
    - 你需要按 id 触发前先确认任务存在
    - 测试时确认种子数据已落库

    何时不用：
    - 只想看执行历史 → 用 scheduler.list_runs
    - 想看单条详情 → 用 scheduler.get_job

    坑：
    - 没区分 leader / follower 进程；返回的是 DB 全集，跟当前进程是否真在跑 cron 无关
    - 没有 next_fire_at 字段（要拿这个走 HTTP /scheduler/jobs 或 admin 页）
  `.trim(),
  inputSchema: z.object({}),
  execute: async () => {
    const jobs = await repo.listAllJobs();
    return {
      count: jobs.length,
      jobs: jobs.map((j) => ({
        jobId: j.jobId,
        cronExpr: j.cronExpr,
        timezone: j.timezone,
        mode: j.mode,
        enabled: j.enabled,
        description: j.description,
        updatedAt: j.updatedAt.toISOString(),
      })),
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// scheduler.get_job
// ────────────────────────────────────────────────────────────────────

export const schedulerGetJobTool = createTool({
  id: "scheduler.get_job",
  description: `
    查询单个定时任务的完整定义（含 payload）。

    何时用：
    - 用户问"daily_btc_deep_dive 的 prompt 是什么"
    - trigger 前想确认 cron / timezone / payload 配置正确

    何时不用：
    - 想列全部 → 用 scheduler.list_jobs

    坑：
    - 不存在时返回 { found: false, jobId }，不抛错
  `.trim(),
  inputSchema: z.object({ jobId: JobIdSchema }),
  execute: async ({ jobId }) => {
    const job = await repo.getJob(jobId);
    if (job === null) return { found: false, jobId };
    return {
      found: true,
      job: {
        ...job,
        createdAt: job.createdAt.toISOString(),
        updatedAt: job.updatedAt.toISOString(),
      },
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// scheduler.set_enabled
// ────────────────────────────────────────────────────────────────────

export const schedulerSetEnabledTool = createTool({
  id: "scheduler.set_enabled",
  description: `
    切换某个定时任务的 enabled 开关。enabled=true 让 cron 开始跑，false 暂停。

    何时用：
    - 用户说"打开 hourly_btc_backfill"/"暂停 daily_btc_deep_dive"
    - 测试时手动 enable 种子任务

    何时不用：
    - 想改 cron 表达式 / 改 payload → 走 HTTP PATCH /scheduler/jobs/:id（tool 没暴露这个）
    - 想立即跑一次 → 用 scheduler.trigger_job（trigger 不依赖 enabled）

    坑：
    - 改了 enabled 后，本进程的 Scheduler 单例最长延迟 60s（轮询周期）才反映；
      如果 SCHEDULER_ENABLED=false 进程根本没起 scheduler，这里只改 DB 不影响 cron
    - 不存在的 jobId 返回 { found: false }
  `.trim(),
  inputSchema: z.object({
    jobId: JobIdSchema,
    enabled: z.boolean(),
  }),
  execute: async ({ jobId, enabled }) => {
    const updated = await repo.updateJob(jobId, { enabled });
    if (updated === null) return { found: false, jobId };
    return {
      found: true,
      jobId: updated.jobId,
      enabled: updated.enabled,
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// scheduler.trigger_job
// ────────────────────────────────────────────────────────────────────

export const schedulerTriggerJobTool = createTool({
  id: "scheduler.trigger_job",
  description: `
    立即触发一次指定 job（不等 cron），写一条 trigger='manual' 的 run 记录。

    返回：{ runId, status: 'success'|'failed'|'timeout', result, error }

    何时用：
    - 用户说"现在跑一下 hourly_btc_backfill"/"trigger daily_btc_deep_dive"
    - 验证一个新建 / 改过 cron 的 job 配置 OK
    - 故障恢复：cron 错过了想立刻补一次

    何时不用：
    - mode='agent' 的 job：当前对话本身就是 agent；让 agent 触发 agent mode 会绕一圈
      重复编排，意义不大。**本 tool 只接受 mode='tool' 的 job**（agent mode 直接返
      { rejected: true, reason: 'agent_mode_not_triggerable_from_agent' }）。
      想测 agent mode 走 CLI \`pnpm scheduler:trigger <id>\` 或 admin 页。
    - 想批量 backfill → 直接调 data.backfill_bars，不用绕 scheduler

    坑：
    - tool mode 内部走完整 hooks + permissions 链 —— 如果 payload.tool 是 deny 列表里
      的（如 paper.submit_order_intent），会拿到 status='failed'
    - 同一 job 还有 running 行时会被 OVERLAP_PREVENTED 拒，等上次跑完再 trigger
  `.trim(),
  inputSchema: z.object({ jobId: JobIdSchema }),
  execute: async ({ jobId }, ctx) => {
    const job = await repo.getJob(jobId);
    if (job === null) return { rejected: true, reason: "job_not_found", jobId };
    if (job.mode !== "tool") {
      return {
        rejected: true,
        reason: "agent_mode_not_triggerable_from_agent",
        jobId,
        message: "agent mode job 不能由 agent 触发；走 CLI 或 admin 页",
      };
    }
    const mastra = (ctx as { mastra?: Mastra } | undefined)?.mastra;
    if (mastra === undefined) {
      return { rejected: true, reason: "ctx_mastra_unavailable", jobId };
    }
    const result = await runJob({
      job,
      mastra,
      scheduledAt: new Date(),
      trigger: "manual",
    });
    return {
      rejected: false,
      runId: result.runId,
      status: result.status,
      result: result.result ?? null,
      error: result.error ?? null,
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// scheduler.list_runs
// ────────────────────────────────────────────────────────────────────

export const schedulerListRunsTool = createTool({
  id: "scheduler.list_runs",
  description: `
    列 scheduler 执行历史，按 started_at DESC 排序。可按 jobId 过滤。

    何时用：
    - 用户问"hourly_btc_backfill 最近跑成功没"/"scheduler 最近的执行结果"
    - 触发后验证 status
    - 排查为什么没跑 / 报什么错

    何时不用：
    - 想看 job 定义而不是 run → 用 scheduler.list_jobs

    坑：
    - result / error 字段超过 8KB 会被截断成 { _truncated: true, _original_bytes, preview }
    - limit 默认 20，最大 100；想看更多走 HTTP /scheduler/runs?limit=
  `.trim(),
  inputSchema: z.object({
    jobId: JobIdSchema.optional(),
    limit: z.number().int().min(1).max(100).default(20),
  }),
  execute: async ({ jobId, limit }) => {
    const runs = await repo.listRuns({ jobId, limit });
    return {
      count: runs.length,
      runs: runs.map((r) => ({
        runId: r.runId,
        jobId: r.jobId,
        status: r.status,
        trigger: r.trigger,
        scheduledAt: r.scheduledAt.toISOString(),
        startedAt: r.startedAt.toISOString(),
        finishedAt: r.finishedAt ? r.finishedAt.toISOString() : null,
        result: r.result,
        error: r.error,
      })),
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// 聚合导出
// ────────────────────────────────────────────────────────────────────

export const schedulerTools = [
  schedulerCreateJobTool,
  schedulerListJobsTool,
  schedulerGetJobTool,
  schedulerSetEnabledTool,
  schedulerTriggerJobTool,
  schedulerListRunsTool,
] as const;

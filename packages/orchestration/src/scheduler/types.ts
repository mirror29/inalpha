/**
 * Scheduler 类型定义（D-9 · 类 Hermes 定时 agent 模式）。
 *
 * 对应表：`scheduler_jobs` 与 `scheduler_runs`（migration 0004）。
 *
 * 两种 job 形态：
 *
 * - `mode: "tool"` —— 直调 Mastra agent 上的某个 tool，走完整 hooks + permissions 链
 *                     适合"零智能"的纯调度场景（如周期性 backfill）
 * - `mode: "agent"` —— 给 orchestrator agent 一个 prompt，由 LLM 自由编排
 *                      适合需要多 tool call 协作的场景（如盘前 deep_dive）
 */

/** Job 基础字段（mode 无关）。 */
export interface ScheduledJobBase {
  jobId: string;
  cronExpr: string;
  timezone: string;
  enabled: boolean;
  description: string | null;
  createdAt: Date;
  updatedAt: Date;
}

/** mode='tool' 时的 payload —— 直调 wiredTools 里某个 tool。 */
export interface ToolJobPayload {
  /** 形如 `data.backfill_bars` / `paper.submit_order_intent`。 */
  tool: string;
  /** 透传给 tool.execute() 的输入。 */
  input: unknown;
}

/** mode='agent' 时的 payload —— 让 LLM 自由编排。 */
export interface AgentJobPayload {
  /** D-9 仅支持 `orchestrator`；预留为未来多 agent 留口子。 */
  agent: "orchestrator";
  prompt: string;
}

/** 区分联合：mode 决定 payload 形态。 */
export type ScheduledJob =
  | (ScheduledJobBase & { mode: "tool"; payload: ToolJobPayload })
  | (ScheduledJobBase & { mode: "agent"; payload: AgentJobPayload });

/** Job 写入/更新时的输入（无 createdAt/updatedAt，由 DB 默认值填）。 */
export type ScheduledJobInput =
  | {
      jobId: string;
      cronExpr: string;
      timezone?: string;
      enabled?: boolean;
      description?: string | null;
      mode: "tool";
      payload: ToolJobPayload;
    }
  | {
      jobId: string;
      cronExpr: string;
      timezone?: string;
      enabled?: boolean;
      description?: string | null;
      mode: "agent";
      payload: AgentJobPayload;
    };

export type RunStatus = "running" | "success" | "failed" | "timeout";
export type RunTrigger = "cron" | "manual";

export interface RunRecord {
  runId: string;
  jobId: string;
  scheduledAt: Date;
  startedAt: Date;
  finishedAt: Date | null;
  status: RunStatus;
  trigger: RunTrigger;
  result: unknown;
  error: unknown;
}

/** runner.completeRun() 的输入。 */
export interface RunCompletion {
  status: Exclude<RunStatus, "running">;
  result?: unknown;
  error?: unknown;
}

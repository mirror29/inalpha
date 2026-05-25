/**
 * Scheduler 的 Postgres 持久层。
 *
 * 职责：
 *
 * - 懒加载一个 `pg.Pool`，按 DATABASE_URL 配置（settings.databaseUrl）
 * - 提供 jobs / runs 的 CRUD（无 ORM，纯参数化 SQL）
 * - Postgres advisory lock —— 保证多副本部署只有 leader 跑 cron
 *
 * 何时用：仅在 Scheduler / API / CLI 层使用；不要在 tool/agent 业务代码里直接 import
 * （那是后端 service 的职责）。
 *
 * 何时不用：单测里用 setPool() 注入 mock，避免连真实库；E2E smoke 才连 docker-compose。
 *
 * 坑：
 *
 * - advisory lock 是 session-scoped —— 必须复用同一条 PG client；释放或断连即失锁
 * - 多副本部署时 lock 落在哪个副本不稳定 —— 这是 by design（任何一个都可以做 leader）
 */
import { Pool, type PoolClient, type PoolConfig } from "pg";

import { getSettings } from "../config.js";
import type {
  RunCompletion,
  RunRecord,
  RunStatus,
  RunTrigger,
  ScheduledJob,
  ScheduledJobInput,
} from "./types.js";

/** Advisory lock id —— ASCII "INAL" big-endian = 0x494E414C。 */
export const SCHEDULER_LOCK_ID = 0x494e414c;

let _pool: Pool | undefined;

/** 懒加载 Pool；测试时用 setPool() 覆盖。 */
export function getPool(): Pool {
  if (_pool !== undefined) return _pool;
  const settings = getSettings();
  if (!settings.databaseUrl) {
    throw new Error(
      "DATABASE_URL 未配置 —— scheduler 需要 Postgres。请在 .env 里设置 DATABASE_URL。",
    );
  }
  const cfg: PoolConfig = { connectionString: settings.databaseUrl, max: 4 };
  _pool = new Pool(cfg);
  return _pool;
}

/** 测试时显式注入 Pool（或 mock）。 */
export function setPool(pool: Pool | undefined): void {
  _pool = pool;
}

/** 关池：进程退出时调，幂等。 */
export async function closePool(): Promise<void> {
  if (_pool === undefined) return;
  const p = _pool;
  _pool = undefined;
  await p.end();
}

// ============ advisory lock ============

/**
 * 尝试拿 scheduler 的 leader 锁。
 *
 * 返回的 client 必须由 caller 通过 releaseSchedulerLock() 归还；锁是 session-scoped。
 * 拿不到时返回 null（已有其他副本是 leader）。
 */
export async function tryAcquireSchedulerLock(): Promise<PoolClient | null> {
  const pool = getPool();
  const client = await pool.connect();
  try {
    const { rows } = await client.query<{ acquired: boolean }>(
      "SELECT pg_try_advisory_lock($1) AS acquired",
      [SCHEDULER_LOCK_ID],
    );
    if (rows[0]?.acquired === true) return client;
    client.release();
    return null;
  } catch (err) {
    client.release();
    throw err;
  }
}

/** 释放 leader 锁 + 归还 client。幂等。 */
export async function releaseSchedulerLock(client: PoolClient): Promise<void> {
  try {
    await client.query("SELECT pg_advisory_unlock($1)", [SCHEDULER_LOCK_ID]);
  } finally {
    client.release();
  }
}

// ============ jobs CRUD ============

/** DB row → typed ScheduledJob（snake_case → camelCase + 联合判别）。 */
function rowToJob(row: Record<string, unknown>): ScheduledJob {
  const mode = row["mode"] as "tool" | "agent";
  const base = {
    jobId: row["job_id"] as string,
    cronExpr: row["cron_expr"] as string,
    timezone: row["timezone"] as string,
    enabled: row["enabled"] as boolean,
    description: (row["description"] as string | null) ?? null,
    createdAt: row["created_at"] as Date,
    updatedAt: row["updated_at"] as Date,
  };
  if (mode === "tool") {
    return {
      ...base,
      mode: "tool",
      payload: row["payload"] as ScheduledJob["payload"] & { tool: string },
    } as ScheduledJob;
  }
  return {
    ...base,
    mode: "agent",
    payload: row["payload"] as ScheduledJob["payload"] & { agent: "orchestrator" },
  } as ScheduledJob;
}

/** 列出所有 enabled=true 的 jobs（Scheduler 启动 + 轮询时调）。 */
export async function listEnabledJobs(): Promise<ScheduledJob[]> {
  const { rows } = await getPool().query(
    `SELECT job_id, cron_expr, timezone, mode, payload, enabled, description, created_at, updated_at
     FROM scheduler_jobs WHERE enabled = TRUE ORDER BY job_id`,
  );
  return rows.map(rowToJob);
}

/** 列出全部 jobs（API GET /scheduler/jobs 用）。 */
export async function listAllJobs(): Promise<ScheduledJob[]> {
  const { rows } = await getPool().query(
    `SELECT job_id, cron_expr, timezone, mode, payload, enabled, description, created_at, updated_at
     FROM scheduler_jobs ORDER BY job_id`,
  );
  return rows.map(rowToJob);
}

/** 按 id 查单条；不存在返回 null。 */
export async function getJob(jobId: string): Promise<ScheduledJob | null> {
  const { rows } = await getPool().query(
    `SELECT job_id, cron_expr, timezone, mode, payload, enabled, description, created_at, updated_at
     FROM scheduler_jobs WHERE job_id = $1`,
    [jobId],
  );
  return rows[0] ? rowToJob(rows[0]) : null;
}

/** 创建 job；如已存在抛错（DB 约束）。 */
export async function createJob(input: ScheduledJobInput): Promise<ScheduledJob> {
  const { rows } = await getPool().query(
    `INSERT INTO scheduler_jobs (job_id, cron_expr, timezone, mode, payload, enabled, description)
     VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
     RETURNING job_id, cron_expr, timezone, mode, payload, enabled, description, created_at, updated_at`,
    [
      input.jobId,
      input.cronExpr,
      input.timezone ?? "UTC",
      input.mode,
      JSON.stringify(input.payload),
      input.enabled ?? true,
      input.description ?? null,
    ],
  );
  return rowToJob(rows[0]);
}

/** 部分更新；返回更新后的 job，不存在返回 null。 */
export async function updateJob(
  jobId: string,
  patch: Partial<{
    cronExpr: string;
    timezone: string;
    enabled: boolean;
    description: string | null;
    mode: "tool" | "agent";
    payload: unknown;
  }>,
): Promise<ScheduledJob | null> {
  const sets: string[] = [];
  const args: unknown[] = [];
  let i = 1;
  if (patch.cronExpr !== undefined) {
    sets.push(`cron_expr = $${i++}`);
    args.push(patch.cronExpr);
  }
  if (patch.timezone !== undefined) {
    sets.push(`timezone = $${i++}`);
    args.push(patch.timezone);
  }
  if (patch.enabled !== undefined) {
    sets.push(`enabled = $${i++}`);
    args.push(patch.enabled);
  }
  if (patch.description !== undefined) {
    sets.push(`description = $${i++}`);
    args.push(patch.description);
  }
  if (patch.mode !== undefined) {
    sets.push(`mode = $${i++}`);
    args.push(patch.mode);
  }
  if (patch.payload !== undefined) {
    sets.push(`payload = $${i++}::jsonb`);
    args.push(JSON.stringify(patch.payload));
  }
  if (sets.length === 0) return await getJob(jobId);
  sets.push(`updated_at = NOW()`);
  args.push(jobId);
  const { rows } = await getPool().query(
    `UPDATE scheduler_jobs SET ${sets.join(", ")} WHERE job_id = $${i}
     RETURNING job_id, cron_expr, timezone, mode, payload, enabled, description, created_at, updated_at`,
    args,
  );
  return rows[0] ? rowToJob(rows[0]) : null;
}

/** 删除 job + 级联删除其 runs。返回是否真的删了一行。 */
export async function deleteJob(jobId: string): Promise<boolean> {
  const { rowCount } = await getPool().query(
    `DELETE FROM scheduler_jobs WHERE job_id = $1`,
    [jobId],
  );
  return (rowCount ?? 0) > 0;
}

// ============ runs CRUD ============

function rowToRun(row: Record<string, unknown>): RunRecord {
  return {
    runId: row["run_id"] as string,
    jobId: row["job_id"] as string,
    scheduledAt: row["scheduled_at"] as Date,
    startedAt: row["started_at"] as Date,
    finishedAt: (row["finished_at"] as Date | null) ?? null,
    status: row["status"] as RunStatus,
    trigger: row["trigger"] as RunTrigger,
    result: row["result"] ?? null,
    error: row["error"] ?? null,
  };
}

/** 插入一条 running 行；返回 run_id。 */
export async function insertRun(args: {
  jobId: string;
  scheduledAt: Date;
  trigger: RunTrigger;
}): Promise<string> {
  const { rows } = await getPool().query<{ run_id: string }>(
    `INSERT INTO scheduler_runs (job_id, scheduled_at, status, trigger)
     VALUES ($1, $2, 'running', $3)
     RETURNING run_id`,
    [args.jobId, args.scheduledAt, args.trigger],
  );
  const row = rows[0];
  if (row === undefined) {
    throw new Error(`insertRun: 未拿到 run_id（job_id=${args.jobId}）`);
  }
  return row.run_id;
}

/** 标记 run 完成（success / failed / timeout）。result/error JSON 截断到 8KB 以下。 */
export async function completeRun(
  runId: string,
  completion: RunCompletion,
): Promise<void> {
  await getPool().query(
    `UPDATE scheduler_runs
     SET status = $2, result = $3::jsonb, error = $4::jsonb, finished_at = NOW()
     WHERE run_id = $1`,
    [
      runId,
      completion.status,
      truncateJson(completion.result),
      truncateJson(completion.error),
    ],
  );
}

/** 查指定 job 是否还有 running 行（防 overlap 双保险）。 */
export async function hasRunningRun(jobId: string): Promise<boolean> {
  const { rows } = await getPool().query<{ n: string }>(
    `SELECT COUNT(*)::TEXT AS n FROM scheduler_runs WHERE job_id = $1 AND status = 'running'`,
    [jobId],
  );
  return Number(rows[0]?.n ?? "0") > 0;
}

/** 列出 runs，按 started_at DESC 排序；可按 jobId 过滤。 */
export async function listRuns(opts: {
  jobId?: string;
  limit?: number;
}): Promise<RunRecord[]> {
  const limit = Math.min(Math.max(opts.limit ?? 50, 1), 500);
  if (opts.jobId !== undefined) {
    const { rows } = await getPool().query(
      `SELECT run_id, job_id, scheduled_at, started_at, finished_at, status, trigger, result, error
       FROM scheduler_runs WHERE job_id = $1 ORDER BY started_at DESC LIMIT $2`,
      [opts.jobId, limit],
    );
    return rows.map(rowToRun);
  }
  const { rows } = await getPool().query(
    `SELECT run_id, job_id, scheduled_at, started_at, finished_at, status, trigger, result, error
     FROM scheduler_runs ORDER BY started_at DESC LIMIT $1`,
    [limit],
  );
  return rows.map(rowToRun);
}

// ============ 辅助 ============

const MAX_JSON_BYTES = 8 * 1024;

/** JSON 序列化超过 8KB 时截断（避免大 result 撑爆表）。 */
function truncateJson(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const s = JSON.stringify(value);
  if (s.length <= MAX_JSON_BYTES) return s;
  return JSON.stringify({
    _truncated: true,
    _original_bytes: s.length,
    preview: s.slice(0, MAX_JSON_BYTES - 256),
  });
}

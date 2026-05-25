/**
 * Scheduler —— croner 单例 + Postgres advisory lock + 60s 轮询 DB。
 *
 * 生命周期：
 *
 * - `start()` —— 尝试拿 advisory lock；拿到则成为 leader，注册 cron + 启动轮询；
 *                拿不到则静默退出（其他副本已经是 leader）
 * - `reload()` —— 从 DB 读 enabled=true 的 jobs，与内存 crons 增量 reconcile
 * - `stop()`   —— 停所有 cron + 关轮询 + 释放 advisory lock
 *
 * 何时用：仅 `mastra/index.ts` 在启动期调一次 `bootstrapScheduler(mastra)`。
 *
 * 何时不用：业务代码不要从这里 import；用 `getScheduler()` 拿单例（API 层用）。
 *
 * 坑：
 *
 * - croner `protect: true` 防止同一 job 重叠触发；DB 的 hasRunningRun 是双保险
 * - reload 失败不抛错，只 log——避免 leader 永久卡死；下一次轮询自然恢复
 * - SIGTERM / SIGINT 触发 stop()，等 in-flight runs 自然完成（最多 5 min agent timeout）
 */
import type { Mastra } from "@mastra/core/mastra";
import { Cron } from "croner";

import { runJob } from "./runner.js";
import { listEnabledJobs, releaseSchedulerLock, tryAcquireSchedulerLock } from "./repo.js";
import type { ScheduledJob } from "./types.js";

/** 内存里缓存的 cron 元数据，用来判断是否需要重新注册。 */
interface CronMeta {
  cronExpr: string;
  timezone: string;
  updatedAt: number;
}

/** 默认 60s 轮一次 DB，捕获 job 增删改。 */
const RELOAD_INTERVAL_MS = 60_000;

export class Scheduler {
  private readonly mastra: Mastra;
  private readonly crons = new Map<string, Cron>();
  private readonly meta = new Map<string, CronMeta>();
  private lockClient: Awaited<ReturnType<typeof tryAcquireSchedulerLock>> = null;
  private pollTimer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly logger: Pick<Console, "info" | "warn" | "error"> = console;

  constructor(mastra: Mastra) {
    this.mastra = mastra;
  }

  /** 启动 —— 试拿 leader 锁；拿不到就静默退出。 */
  async start(): Promise<void> {
    if (this.running) return;
    try {
      const lock = await tryAcquireSchedulerLock();
      if (lock === null) {
        this.logger.info(
          JSON.stringify({
            evt: "scheduler.leader_skip",
            msg: "scheduler: 拿不到 advisory lock，本副本不当 leader",
          }),
        );
        return;
      }
      this.lockClient = lock;
      this.running = true;
      await this.reload();
      this.pollTimer = setInterval(() => {
        void this.reload().catch((err: unknown) => {
          this.logger.error(
            JSON.stringify({
              evt: "scheduler.reload_failed",
              error: err instanceof Error ? err.message : String(err),
            }),
          );
        });
      }, RELOAD_INTERVAL_MS);
      this.logger.info(
        JSON.stringify({
          evt: "scheduler.started",
          jobs: Array.from(this.crons.keys()),
        }),
      );
    } catch (err) {
      this.running = false;
      this.logger.error(
        JSON.stringify({
          evt: "scheduler.start_failed",
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  }

  /** 停止 —— 关 cron + 关轮询 + 释放锁。幂等。 */
  async stop(): Promise<void> {
    if (!this.running) return;
    this.running = false;
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    for (const cron of this.crons.values()) {
      cron.stop();
    }
    this.crons.clear();
    this.meta.clear();
    if (this.lockClient !== null) {
      await releaseSchedulerLock(this.lockClient).catch(() => undefined);
      this.lockClient = null;
    }
    this.logger.info(JSON.stringify({ evt: "scheduler.stopped" }));
  }

  /**
   * 从 DB 拉最新 enabled jobs，与内存 crons 增量同步。
   *
   * - 新增（DB 有 / 内存无）→ 注册
   * - 修改（cron_expr / timezone / updated_at 变了）→ 重新注册
   * - 消失（DB 没了或 enabled=false）→ 停 cron + 清缓存
   */
  async reload(): Promise<void> {
    const jobs = await listEnabledJobs();
    const seen = new Set<string>();

    for (const job of jobs) {
      seen.add(job.jobId);
      const existing = this.meta.get(job.jobId);
      if (
        existing === undefined ||
        existing.cronExpr !== job.cronExpr ||
        existing.timezone !== job.timezone ||
        existing.updatedAt !== job.updatedAt.getTime()
      ) {
        this.crons.get(job.jobId)?.stop();
        const cron = this.buildCron(job);
        this.crons.set(job.jobId, cron);
        this.meta.set(job.jobId, {
          cronExpr: job.cronExpr,
          timezone: job.timezone,
          updatedAt: job.updatedAt.getTime(),
        });
      }
    }

    for (const id of Array.from(this.crons.keys())) {
      if (!seen.has(id)) {
        this.crons.get(id)?.stop();
        this.crons.delete(id);
        this.meta.delete(id);
      }
    }
  }

  /** 列出当前 leader 注册的 cron + 下次触发时间（HTTP API 用）。 */
  listActiveJobs(): Array<{ jobId: string; nextFireAt: Date | null }> {
    return Array.from(this.crons.entries()).map(([jobId, cron]) => ({
      jobId,
      nextFireAt: (cron.nextRun() as Date | null) ?? null,
    }));
  }

  /** 是否在跑（本副本是否拿到 leader 锁）。 */
  isRunning(): boolean {
    return this.running;
  }

  // ------------------------ private ------------------------

  private buildCron(job: ScheduledJob): Cron {
    // protect: true —— 上次 fire 还没跑完时跳过本次
    return new Cron(
      job.cronExpr,
      { timezone: job.timezone, protect: true },
      async () => {
        const scheduledAt = new Date();
        try {
          const result = await runJob({
            job,
            mastra: this.mastra,
            scheduledAt,
            trigger: "cron",
          });
          this.logger.info(
            JSON.stringify({
              evt: "scheduler.run_done",
              jobId: job.jobId,
              runId: result.runId,
              status: result.status,
            }),
          );
        } catch (err) {
          // runJob 内部已经吞错并写 runs 表；这里只作兜底
          this.logger.error(
            JSON.stringify({
              evt: "scheduler.run_unhandled",
              jobId: job.jobId,
              error: err instanceof Error ? err.message : String(err),
            }),
          );
        }
      },
    );
  }
}

// ============ 单例 ============

let _instance: Scheduler | null = null;

/** 启动（或返回已有的）单例。在 mastra/index.ts 末尾调用。 */
export function bootstrapScheduler(mastra: Mastra): Scheduler {
  if (_instance !== null) return _instance;
  _instance = new Scheduler(mastra);
  void _instance.start();
  registerShutdownHooks(_instance);
  return _instance;
}

/** 拿当前单例，未 bootstrap 返回 null（HTTP API 防御性查询用）。 */
export function getScheduler(): Scheduler | null {
  return _instance;
}

/** 测试时清单例。 */
export function resetSchedulerForTest(): void {
  _instance = null;
}

let _shutdownHooked = false;
function registerShutdownHooks(scheduler: Scheduler): void {
  if (_shutdownHooked) return;
  _shutdownHooked = true;
  const stop = (): void => {
    void scheduler.stop();
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
}

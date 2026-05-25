/**
 * Scheduler HTTP API —— 挂到 Mastra `server.apiRoutes` 上，跟 mastra dev 共用 4111 端口。
 *
 * 端点：
 *
 * - `GET    /scheduler/jobs`              —— 列全部 jobs + 下次触发时间
 * - `POST   /scheduler/jobs`              —— 创建
 * - `GET    /scheduler/jobs/:id`          —— 查单条
 * - `PATCH  /scheduler/jobs/:id`          —— 更新（部分字段，如 enabled）
 * - `DELETE /scheduler/jobs/:id`          —— 删除（级联清 runs）
 * - `POST   /scheduler/jobs/:id/trigger`  —— 立即触发一次（trigger='manual'）
 * - `GET    /scheduler/runs`              —— 列 runs（按 job_id / limit 过滤）
 *
 * 何时用：static admin HTML、外部脚本、未来 Next.js 前端集成。
 *
 * 何时不用：业务代码（agent / tool）不要从 HTTP 调度 —— 直接 import repo / runner。
 *
 * 鉴权：跟 mastra dev 一致（依赖 Mastra 内置的 protected/public path 配置）；
 * 暂未叠加额外 JWT 校验，本地 dev 信任 4111 端口；生产部署应加反代鉴权。
 */
import type { Context, Handler } from "hono";

import { getScheduler } from "./index.js";
import * as repo from "./repo.js";
import { runJob } from "./runner.js";
import type { ScheduledJobInput } from "./types.js";

interface ApiRouteSpec {
  path: string;
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  handler: Handler;
}

function badRequest(c: Context, msg: string, details?: unknown): Response {
  return c.json({ error: "bad_request", message: msg, details }, 400);
}

function notFound(c: Context, id: string): Response {
  return c.json({ error: "not_found", jobId: id }, 404);
}

// ============ Handlers ============

/** GET /scheduler/jobs */
const listJobs: Handler = async (c: Context) => {
  const jobs = await repo.listAllJobs();
  const sched = getScheduler();
  const activeMap = new Map<string, Date | null>(
    sched?.listActiveJobs().map((a) => [a.jobId, a.nextFireAt]) ?? [],
  );
  return c.json({
    schedulerRunning: sched?.isRunning() ?? false,
    jobs: jobs.map((j) => ({ ...j, nextFireAt: activeMap.get(j.jobId) ?? null })),
  });
};

/** GET /scheduler/jobs/:id */
const getJob: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  const job = await repo.getJob(id);
  return job === null ? notFound(c, id) : c.json(job);
};

/** POST /scheduler/jobs */
const createJob: Handler = async (c: Context) => {
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    return badRequest(c, "invalid JSON body");
  }
  const parsed = parseJobInput(body);
  if (!parsed.ok) return badRequest(c, parsed.error);
  try {
    const job = await repo.createJob(parsed.value);
    void getScheduler()?.reload();
    return c.json(job, 201);
  } catch (err) {
    return c.json(
      { error: "create_failed", message: err instanceof Error ? err.message : String(err) },
      400,
    );
  }
};

/** PATCH /scheduler/jobs/:id */
const updateJob: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  let patch: Record<string, unknown>;
  try {
    patch = (await c.req.json()) as Record<string, unknown>;
  } catch {
    return badRequest(c, "invalid JSON body");
  }
  const allowed: Parameters<typeof repo.updateJob>[1] = {};
  if (typeof patch["cronExpr"] === "string") allowed.cronExpr = patch["cronExpr"];
  if (typeof patch["timezone"] === "string") allowed.timezone = patch["timezone"];
  if (typeof patch["enabled"] === "boolean") allowed.enabled = patch["enabled"];
  if (patch["description"] === null || typeof patch["description"] === "string") {
    allowed.description = patch["description"] as string | null;
  }
  if (patch["mode"] === "tool" || patch["mode"] === "agent") allowed.mode = patch["mode"];
  if (patch["payload"] !== undefined) allowed.payload = patch["payload"];

  const updated = await repo.updateJob(id, allowed);
  if (updated === null) return notFound(c, id);
  void getScheduler()?.reload();
  return c.json(updated);
};

/** DELETE /scheduler/jobs/:id */
const deleteJob: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  const ok = await repo.deleteJob(id);
  if (!ok) return notFound(c, id);
  void getScheduler()?.reload();
  return c.json({ ok: true, jobId: id });
};

/** POST /scheduler/jobs/:id/trigger */
const triggerJob: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  const job = await repo.getJob(id);
  if (job === null) return notFound(c, id);
  const mastra = c.get("mastra");
  if (mastra === undefined) {
    return c.json({ error: "mastra_unavailable" }, 500);
  }
  const result = await runJob({
    job,
    mastra,
    scheduledAt: new Date(),
    trigger: "manual",
  });
  return c.json(result);
};

/** GET /scheduler/runs?job_id=&limit= */
const listRuns: Handler = async (c: Context) => {
  const jobId = c.req.query("job_id");
  const limitRaw = c.req.query("limit");
  const limit = limitRaw ? Number(limitRaw) : 50;
  if (Number.isNaN(limit) || limit <= 0) return badRequest(c, "invalid limit");
  const runs = await repo.listRuns({ jobId, limit });
  return c.json({ runs });
};

// ============ Input parsing ============

interface ParseResult<T> {
  ok: boolean;
  value?: T;
  error?: string;
}

function parseJobInput(body: unknown): { ok: true; value: ScheduledJobInput } | { ok: false; error: string } {
  if (body === null || typeof body !== "object") {
    return { ok: false, error: "body must be a JSON object" };
  }
  const b = body as Record<string, unknown>;
  if (typeof b["jobId"] !== "string" || b["jobId"].length === 0) {
    return { ok: false, error: "jobId must be a non-empty string" };
  }
  if (typeof b["cronExpr"] !== "string" || b["cronExpr"].length === 0) {
    return { ok: false, error: "cronExpr must be a non-empty string" };
  }
  if (b["mode"] !== "tool" && b["mode"] !== "agent") {
    return { ok: false, error: "mode must be 'tool' or 'agent'" };
  }
  const payload = b["payload"];
  if (payload === null || typeof payload !== "object") {
    return { ok: false, error: "payload must be an object" };
  }
  if (b["mode"] === "tool") {
    const p = payload as Record<string, unknown>;
    if (typeof p["tool"] !== "string") {
      return { ok: false, error: "tool mode requires payload.tool: string" };
    }
    return {
      ok: true,
      value: {
        jobId: b["jobId"],
        cronExpr: b["cronExpr"],
        timezone: typeof b["timezone"] === "string" ? b["timezone"] : "UTC",
        enabled: typeof b["enabled"] === "boolean" ? b["enabled"] : true,
        description: typeof b["description"] === "string" ? b["description"] : null,
        mode: "tool",
        payload: { tool: p["tool"], input: p["input"] ?? {} },
      },
    };
  }
  const p = payload as Record<string, unknown>;
  if (p["agent"] !== "orchestrator") {
    return { ok: false, error: "agent mode requires payload.agent: 'orchestrator'" };
  }
  if (typeof p["prompt"] !== "string" || p["prompt"].length === 0) {
    return { ok: false, error: "agent mode requires payload.prompt: non-empty string" };
  }
  return {
    ok: true,
    value: {
      jobId: b["jobId"],
      cronExpr: b["cronExpr"],
      timezone: typeof b["timezone"] === "string" ? b["timezone"] : "UTC",
      enabled: typeof b["enabled"] === "boolean" ? b["enabled"] : true,
      description: typeof b["description"] === "string" ? b["description"] : null,
      mode: "agent",
      payload: { agent: "orchestrator", prompt: p["prompt"] },
    },
  };
}

// ============ Routes export ============

export const schedulerApiRoutes: ApiRouteSpec[] = [
  { path: "/scheduler/jobs", method: "GET", handler: listJobs },
  { path: "/scheduler/jobs", method: "POST", handler: createJob },
  { path: "/scheduler/jobs/:id", method: "GET", handler: getJob },
  { path: "/scheduler/jobs/:id", method: "PATCH", handler: updateJob },
  { path: "/scheduler/jobs/:id", method: "DELETE", handler: deleteJob },
  { path: "/scheduler/jobs/:id/trigger", method: "POST", handler: triggerJob },
  { path: "/scheduler/runs", method: "GET", handler: listRuns },
];

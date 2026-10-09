/**
 * Permissions HTTP API —— 挂到 Mastra ``server.apiRoutes`` 上，跟 mastra dev 共用 4111 端口。
 *
 * 端点（ADR-0018 / D-9.1b；history 为 D-12）：
 *
 * - ``GET  /permissions/pending``           —— 列出当前挂起的 ask 审批
 * - ``GET  /permissions/history``           —— 审批审计历史（含终态，Postgres）
 * - ``POST /permissions/:id/respond``       —— 前端决策（``{decision: 'allow'|'deny'}``）
 *
 * 何时用：前端气泡（CopilotKit / Mastra Studio）轮询 list 显示 + 用户点按钮触发 respond。
 *
 * 鉴权：identityMiddleware 验签 JWT 后提供 owner；所有查询与决策必须绑定该主体。
 *
 * 不在范围：SSE 推送 / WebSocket —— MVP 用前端轮询（1-2s 间隔够用，挂起项数量小）。
 */
import type { Context, Handler } from "hono";

import { pendingApprovals } from "./pending.js";
import { listHistory } from "./repo.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";

interface ApiRouteSpec {
  path: string;
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  handler: Handler;
}

function getAuthSub(c: Context): string | undefined {
  const requestContext = c.get("requestContext") as
    | { get?: (key: string) => unknown }
    | undefined;
  const value =
    typeof requestContext?.get === "function" ? requestContext.get(AUTH_SUB_KEY) : undefined;
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

const listPending: Handler = (c: Context) => {
  const authSub = getAuthSub(c);
  if (!authSub) return c.json({ error: "unauthorized" }, 401);
  return c.json({ pending: pendingApprovals.list(authSub) });
};

const listApprovalHistory: Handler = async (c: Context) => {
  const authSub = getAuthSub(c);
  if (!authSub) return c.json({ error: "unauthorized" }, 401);
  const rawLimit = Number(c.req.query("limit") ?? "50");
  const limit = Number.isFinite(rawLimit) ? rawLimit : 50;
  try {
    return c.json({ history: await listHistory(authSub, limit) });
  } catch (err) {
    console.error("[permissions] listHistory 失败:", err);
    return c.json({ error: "history_unavailable" }, 503);
  }
};

const respondPending: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  if (!id) {
    return c.json({ error: "bad_request", message: "missing :id" }, 400);
  }
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request", message: "expected JSON body" }, 400);
  }
  const decision = (body as { decision?: unknown } | null)?.decision;
  if (decision !== "allow" && decision !== "deny") {
    return c.json(
      {
        error: "bad_request",
        message: "decision must be 'allow' or 'deny'",
        got: decision,
      },
      400,
    );
  }
  const authSub = getAuthSub(c);
  if (!authSub) return c.json({ error: "unauthorized" }, 401);
  const ok = pendingApprovals.respond(id, decision, authSub);
  if (decision === "allow") {
    try {
      const execution = await pendingApprovals.dispatchApproved(id, authSub, { requestContext: c.get("requestContext") });
      if (execution !== undefined) return c.json({ ok: true, decision, requestId: id, execution });
    } catch {
      return c.json({ error: "approved_execution_unavailable", requestId: id }, 503);
    }
  }
  if (!ok) {
    return c.json({ error: "not_found_or_expired", requestId: id }, 404);
  }
  return c.json({ ok: true, decision, requestId: id });
};

export const permissionsApiRoutes: ApiRouteSpec[] = [
  { path: "/permissions/pending", method: "GET", handler: listPending },
  { path: "/permissions/history", method: "GET", handler: listApprovalHistory },
  { path: "/permissions/:id/respond", method: "POST", handler: respondPending },
];

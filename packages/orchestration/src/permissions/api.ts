/**
 * Permissions HTTP API —— 挂到 Mastra ``server.apiRoutes`` 上，跟 mastra dev 共用 4111 端口。
 *
 * 端点（ADR-0018 / D-9.1b）：
 *
 * - ``GET  /permissions/pending``           —— 列出当前挂起的 ask 审批
 * - ``POST /permissions/:id/respond``       —— 前端决策（``{decision: 'allow'|'deny'}``）
 *
 * 何时用：前端气泡（CopilotKit / Mastra Studio）轮询 list 显示 + 用户点按钮触发 respond。
 *
 * 鉴权：跟 scheduler api 一致，依赖 Mastra dev 端 protected/public 配置；生产部署应加反代鉴权。
 *
 * 不在范围：SSE 推送 / WebSocket —— MVP 用前端轮询（1-2s 间隔够用，挂起项数量小）。
 */
import type { Context, Handler } from "hono";

import { pendingApprovals } from "./pending.js";

interface ApiRouteSpec {
  path: string;
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  handler: Handler;
}

const listPending: Handler = (c: Context) => {
  return c.json({ pending: pendingApprovals.list() });
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
  const ok = pendingApprovals.respond(id, decision);
  if (!ok) {
    return c.json({ error: "not_found_or_expired", requestId: id }, 404);
  }
  return c.json({ ok: true, decision, requestId: id });
};

export const permissionsApiRoutes: ApiRouteSpec[] = [
  { path: "/permissions/pending", method: "GET", handler: listPending },
  { path: "/permissions/:id/respond", method: "POST", handler: respondPending },
];

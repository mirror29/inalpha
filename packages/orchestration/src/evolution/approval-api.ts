/** Model-free creation of a frozen cost approval using the same hooks as chat. */
import type { Handler } from "hono";
import { verifyToken } from "../auth.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";
import { loopInputSchema, evolverStartLoopTool } from "../tools/evolution-loop.js";
import { pendingApprovals } from "../permissions/pending.js";
import { wireToolList } from "../mastra/tool-wiring.js";

const tool = wireToolList([evolverStartLoopTool])[0]!;

/** Request frozen consent through normal hooks; an already approved identical command may resume. */
const handler: Handler = async c => {
  const token = c.req.header("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return c.json({ error: "unauthorized" }, 401);
  try {
    const owner = await verifyToken(token);
    const rc = c.get("requestContext") as Map<string, unknown> | undefined;
    if (!owner.sub || owner.token_use !== undefined || rc?.get(AUTH_SUB_KEY) !== owner.sub) return c.json({ error: "unauthorized" }, 401);
    const raw = await c.req.json();
    const input = loopInputSchema.safeParse(raw);
    if (!input.success || !input.data.experiment) return c.json({ error: "EXPERIMENT_PREPARATION_REQUIRED" }, 400);
    /** A stable owner-and-draft scope deduplicates repeated preparation clicks. */
    rc.set("sessionId", `experiment:${input.data.targetId}:${input.data.experiment.eventSnapshotId}`);
    return c.json(await tool.execute!(input.data, { requestContext: rc }));
  } catch { return c.json({ error: "EXPERIMENT_APPROVAL_UNAVAILABLE" }, 503); }
};

/** Read an owner-scoped pending summary or recovery entitlement; GET never executes. */
const statusHandler: Handler = async c => {
  const token = c.req.header("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return c.json({ error: "unauthorized" }, 401);
  try {
    const owner = await verifyToken(token);
    if (!owner.sub || owner.token_use !== undefined) return c.json({ error: "unauthorized" }, 401);
    const id = c.req.param("requestId")!;
    const status = await pendingApprovals.status(id, owner.sub);
    const pending = pendingApprovals.list(owner.sub).find(item => item.requestId === id && item.toolName === "evolver.start_evolution_loop");
    c.header("Cache-Control", "no-store");
    if (status.status === "pending" && !pending) return c.json({ status: "unavailable" });
    return c.json({ ...status, requestId: id, ...(pending ? { toolInput: pending.toolInput } : {}) });
  } catch { return c.json({ error: "EXPERIMENT_APPROVAL_UNAVAILABLE" }, 503); }
};

export const experimentApprovalApiRoutes = [
  { path: "/evolution/approval", method: "POST" as const, handler },
  { path: "/evolution/approval/:requestId", method: "GET" as const, handler: statusHandler },
];

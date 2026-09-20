/** Renew short-lived credential access only for an authorized, fenced loop worker. */
import type { Handler } from "hono";
import { jwtVerify } from "jose";
import { Pool } from "pg";
import { z } from "zod";

import { getSettings } from "../config.js";
import { mintEvolutionCredentialGrant } from "../mastra/llm/evolution-credential-grant.js";
import type { EvolutionLLMSnapshot } from "../mastra/llm/evolution-snapshot.js";

const scopeSchema = z.object({
  loop_id: z.string().uuid(),
  owner_account_id: z.string().uuid(),
  lease_token: z.string().uuid(),
  phase: z.enum(["baseline", "campaign"]),
});

let pool: Pool | undefined;

/** Close the dedicated small pool during orchestration shutdown. */
export async function closeLoopCredentialPool(): Promise<void> {
  const current = pool;
  pool = undefined;
  await current?.end();
}

/** Return no credential when the persisted authorization, budget, or lease is stale. */
export async function findCredentialScope(scope: z.infer<typeof scopeSchema>) {
  pool ??= new Pool({ connectionString: getSettings().databaseUrl, max: 2 });
  const result = await pool.query<{
    requested_by_sub: string;
    operation_id: string;
    request_digest: string;
    llm_snapshot: EvolutionLLMSnapshot;
  }>(
    `SELECT l.requested_by_sub,l.operation_id,a.request_digest,a.llm_snapshot
     FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
     LEFT JOIN strategy_evo_runs r ON r.run_id=l.e1_run_id AND r.owner_account_id=l.owner_account_id
     LEFT JOIN evolution_campaigns c ON c.campaign_id=l.campaign_id AND c.owner_account_id=l.owner_account_id
     WHERE l.loop_id=$1 AND l.owner_account_id=$2 AND a.owner_account_id=$2
       AND a.revoked_at IS NULL AND a.expires_at>clock_timestamp()
       AND a.spent_usd+a.reserved_usd<a.max_cost_usd
       AND l.status IN ('target_resolved','baseline_ready','campaign_running')
       AND (($4='baseline' AND l.campaign_id IS NULL AND r.status='running'
             AND l.lease_token=$3 AND l.lease_expires_at>=clock_timestamp())
         OR ($4='campaign' AND c.status='replaying'
             AND c.lease_token=$3 AND c.lease_expires_at>=clock_timestamp()))`,
    [scope.loop_id, scope.owner_account_id, scope.lease_token, scope.phase],
  );
  return result.rows[0];
}

/** Authenticate the service independently of user-facing Mastra identity middleware. */
export function createLoopCredentialHandler(
  lookup: typeof findCredentialScope = findCredentialScope,
): Handler {
  return async (c) => {
    const token = c.req.header("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
    if (!token) return c.json({ error: "unauthorized" }, 401);
    let scope: z.infer<typeof scopeSchema>;
    try {
      const { payload } = await jwtVerify(token, new TextEncoder().encode(getSettings().jwtSecret), {
        algorithms: ["HS256"], audience: "inalpha-orchestration",
        requiredClaims: ["sub", "iat", "exp", "aud"],
      });
      const now = Math.floor(Date.now() / 1000);
      if (payload.sub !== "service:evolver" || payload.token_use !== "service"
        || payload.token_purpose !== "evolution_loop_credential"
        || typeof payload.iat !== "number" || typeof payload.exp !== "number"
        || payload.iat > now || payload.exp <= payload.iat || payload.exp - payload.iat > 300) {
        return c.json({ error: "forbidden" }, 403);
      }
      scope = scopeSchema.parse(payload);
      if (scope.loop_id !== c.req.param("loopId")) return c.json({ error: "forbidden" }, 403);
    } catch {
      return c.json({ error: "unauthorized" }, 401);
    }
    try {
      const authorized = await lookup(scope);
      if (!authorized) return c.json({ error: "loop_authorization_unavailable" }, 403);
      const grant = await mintEvolutionCredentialGrant({
        authSub: authorized.requested_by_sub,
        operationId: authorized.operation_id,
        requestDigest: authorized.request_digest,
        snapshot: authorized.llm_snapshot,
        purpose: scope.phase === "baseline" ? "e1_run" : "event_campaign",
        loopScope: { loopId: scope.loop_id, leaseToken: scope.lease_token, phase: scope.phase },
      });
      c.header("Cache-Control", "no-store");
      return c.json({ credential_grant: grant });
    } catch {
      return c.json({ error: "loop_credential_unavailable" }, 503);
    }
  };
}

export const loopCredentialApiRoutes = [{
  path: "/internal/evolution-loops/:loopId/credential-grant",
  method: "POST" as const,
  handler: createLoopCredentialHandler(),
}];

import type { JWTPayload } from "jose";

import { getPool } from "@/lib/db";

/** Re-check durable authorization at redemption, including lease revocation after issuance. */
export async function validLoopCredential(payload: JWTPayload): Promise<boolean> {
  if (payload.loop_id === undefined) return true;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (typeof payload.loop_id !== "string" || !uuid.test(payload.loop_id)
    || typeof payload.lease_token !== "string" || !uuid.test(payload.lease_token)
    || !["baseline", "campaign"].includes(String(payload.loop_phase))
    || payload.grant_purpose !== (payload.loop_phase === "baseline" ? "e1_run" : "event_campaign")
    || typeof payload.exp !== "number" || typeof payload.iat !== "number"
    || payload.exp - payload.iat > 300) return false;
  const result = await getPool().query(
    `SELECT l.loop_id FROM evolution_loops l
     JOIN evolution_loop_authorizations a USING(loop_id)
     LEFT JOIN strategy_evo_runs r ON r.run_id=l.e1_run_id AND r.owner_account_id=l.owner_account_id
     LEFT JOIN evolution_campaigns c ON c.campaign_id=l.campaign_id AND c.owner_account_id=l.owner_account_id
     WHERE l.loop_id=$1 AND l.requested_by_sub=$2 AND l.operation_id=$3
       AND a.owner_account_id=l.owner_account_id AND a.request_digest=$4
       AND a.llm_snapshot->>'config_id'=$5 AND a.llm_snapshot->>'config_digest'=$6
       AND a.revoked_at IS NULL AND a.expires_at>clock_timestamp()
       AND a.spent_usd+a.reserved_usd<a.max_cost_usd
       AND l.status IN ('target_resolved','baseline_ready','campaign_running')
       AND (($7='baseline' AND l.campaign_id IS NULL AND r.status='running'
             AND l.lease_token=$8 AND l.lease_expires_at>=clock_timestamp())
         OR ($7='campaign' AND c.status='replaying'
             AND c.lease_token=$8 AND c.lease_expires_at>=clock_timestamp()))`,
    [payload.loop_id, payload.sub, payload.operation_id, payload.request_digest,
      payload.config_id, payload.llm_config_digest, payload.loop_phase, payload.lease_token],
  );
  return result.rowCount === 1;
}

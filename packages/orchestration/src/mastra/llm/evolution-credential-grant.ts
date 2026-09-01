/** Evolver 可转交、但自身不能签发的 owner 凭据授权。 */
import { createPrivateKey, randomUUID } from "node:crypto";

import { SignJWT } from "jose";

import type { EvolutionLLMSnapshot } from "./evolution-snapshot.js";

const GRANT_AUDIENCE = "inalpha-dashboard-credential";
const GRANT_TTL_SECONDS = 30 * 60 * 60;

/**
 * 为一次已审批的演化操作签发短效凭据 capability。
 *
 * 私钥只应注入 orchestration；Evolver 仅转交 token，不能伪造任意 owner/config。
 */
export async function mintEvolutionCredentialGrant(args: {
  authSub: string;
  operationId: string;
  requestDigest: string;
  snapshot: EvolutionLLMSnapshot;
  purpose?: "e1_run" | "event_campaign";
}): Promise<string> {
  const encoded = process.env.EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64?.trim();
  if (!encoded) {
    throw new Error("EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64 is required for evolution");
  }
  let privateKey: ReturnType<typeof createPrivateKey>;
  try {
    privateKey = createPrivateKey({
      key: Buffer.from(encoded, "base64"),
      format: "der",
      type: "pkcs8",
    });
  } catch {
    throw new Error("EVOLUTION_CREDENTIAL_PRIVATE_KEY_B64 is not a valid PKCS8 key");
  }
  const now = Math.floor(Date.now() / 1_000);
  return await new SignJWT({
    token_use: "evolution_credential",
    config_id: args.snapshot.config_id,
    provider: args.snapshot.provider,
    operation_id: args.operationId,
    grant_purpose: args.purpose ?? "e1_run",
    request_digest: args.requestDigest,
    llm_config_digest: args.snapshot.config_digest,
  })
    .setProtectedHeader({ alg: "EdDSA", typ: "JWT" })
    .setSubject(args.authSub)
    .setAudience(["inalpha-evolver", GRANT_AUDIENCE])
    .setJti(randomUUID())
    .setIssuedAt(now)
    .setExpirationTime(now + GRANT_TTL_SECONDS)
    .sign(privateKey);
}

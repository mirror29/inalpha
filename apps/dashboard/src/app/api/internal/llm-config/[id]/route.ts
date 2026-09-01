import { createPublicKey } from "node:crypto";

import { jwtVerify } from "jose";
import { NextRequest, NextResponse } from "next/server";

import { decryptUserApiKey } from "@/lib/user-preferences";
import { getPool } from "@/lib/db";

const MAX_CREDENTIAL_TTL_SECONDS = 30 * 60 * 60;
const GRANT_AUDIENCE = "inalpha-dashboard-credential";

function publicKey(): ReturnType<typeof createPublicKey> {
  const value = process.env.EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64?.trim();
  if (!value) throw new Error("EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64 is required");
  return createPublicKey({ key: Buffer.from(value, "base64"), format: "der", type: "spki" });
}

/** Resolves an existing encrypted owner credential for the Evolver service only. */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const configId = (await params).id;
  const raw = request.headers.get("authorization");
  const token = raw?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!token) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  let subject: string;
  try {
    const { payload } = await jwtVerify(token, publicKey(), {
      algorithms: ["EdDSA"],
      audience: GRANT_AUDIENCE,
      requiredClaims: ["sub", "jti", "iat", "exp", "aud"],
    });
    const issuedAt = payload.iat;
    const expiresAt = payload.exp;
    const now = Math.floor(Date.now() / 1_000);
    const grantPurpose = payload.grant_purpose ?? "e1_run";
    if (
      payload.token_use !== "evolution_credential" ||
      payload.config_id !== configId ||
      typeof payload.operation_id !== "string" ||
      payload.operation_id.length < 8 ||
      (grantPurpose !== "e1_run" && grantPurpose !== "event_campaign") ||
      typeof payload.llm_config_digest !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.llm_config_digest) ||
      typeof payload.request_digest !== "string" ||
      !/^[0-9a-f]{64}$/.test(payload.request_digest) ||
      typeof payload.jti !== "string" ||
      !payload.jti ||
      typeof payload.sub !== "string" ||
      !payload.sub ||
      typeof issuedAt !== "number" ||
      typeof expiresAt !== "number" ||
      expiresAt <= issuedAt ||
      expiresAt - issuedAt > MAX_CREDENTIAL_TTL_SECONDS ||
      issuedAt > now
    ) {
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    }
    subject = payload.sub;

    let config;
    try {
      config = await decryptUserApiKey(subject, configId);
    } catch {
      return NextResponse.json({ error: "credential_store_unavailable" }, { status: 503 });
    }
    if (!config) return NextResponse.json({ error: "not_found" }, { status: 404 });

    let recorded;
    const maxRedemptions = grantPurpose === "event_campaign" ? 8 : 2;
    const redemptionWindow = grantPurpose === "event_campaign" ? "30 hours" : "2 minutes";
    try {
      recorded = await getPool().query(
        `INSERT INTO evolution_credential_grant_uses
         (jti,owner_sub,config_id,operation_id,config_digest,request_digest,grant_purpose,consumed_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
         ON CONFLICT (jti) DO UPDATE SET
           redemption_count=evolution_credential_grant_uses.redemption_count+1,
           last_redeemed_at=NOW()
         WHERE evolution_credential_grant_uses.owner_sub=EXCLUDED.owner_sub
           AND evolution_credential_grant_uses.config_id=EXCLUDED.config_id
           AND evolution_credential_grant_uses.operation_id=EXCLUDED.operation_id
           AND evolution_credential_grant_uses.config_digest=EXCLUDED.config_digest
           AND evolution_credential_grant_uses.request_digest=EXCLUDED.request_digest
           AND evolution_credential_grant_uses.grant_purpose=EXCLUDED.grant_purpose
           AND evolution_credential_grant_uses.redemption_count<$8
           AND evolution_credential_grant_uses.consumed_at>=NOW()-$9::INTERVAL
         RETURNING jti`,
        [
          payload.jti,
          subject,
          configId,
          payload.operation_id,
          payload.llm_config_digest,
          payload.request_digest,
          grantPurpose,
          maxRedemptions,
          redemptionWindow,
        ],
      );
    } catch {
      return NextResponse.json({ error: "credential_ledger_unavailable" }, { status: 503 });
    }
    if (recorded.rowCount !== 1) {
      return NextResponse.json({ error: "credential_grant_scope_conflict" }, { status: 409 });
    }
    return NextResponse.json(
      {
        config_id: config.id,
        provider: config.provider,
        model: config.model ?? null,
        base_url: config.custom_base_url ?? null,
        api_key: config.api_key,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
}

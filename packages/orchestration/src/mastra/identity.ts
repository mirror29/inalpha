import type { MiddlewareHandler } from "hono";

import { verifyToken } from "../auth.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";
import { userLLMStore, type UserLLMConfig } from "./llm/provider.js";

let warnedNoRequestContext = false;
let warnedAuthSignature = false;

/** Parses a user-owned LLM configuration without logging the raw header. */
export function parseUserLLMConfigHeader(raw: string | undefined): UserLLMConfig | undefined {
  if (!raw?.trim()) return undefined;
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if (
    typeof parsed.provider !== "string" ||
    typeof parsed.api_key !== "string" ||
    parsed.api_key.trim() === ""
  ) {
    return undefined;
  }
  return {
    id: typeof parsed.id === "string" ? parsed.id : "req",
    provider: parsed.provider as UserLLMConfig["provider"],
    model: typeof parsed.model === "string" ? parsed.model : undefined,
    api_key: parsed.api_key,
    custom_base_url: typeof parsed.custom_base_url === "string" ? parsed.custom_base_url : undefined,
    custom_provider_name:
      typeof parsed.custom_provider_name === "string" ? parsed.custom_provider_name : undefined,
    label: typeof parsed.label === "string" ? parsed.label : undefined,
  };
}

/** Injects authenticated identity and the user-owned LLM configuration into request scope. */
export const identityMiddleware: MiddlewareHandler = async (c, next) => {
  let userConfig: UserLLMConfig | undefined;
  try {
    userConfig = parseUserLLMConfigHeader(c.req.header("X-LLM-Config"));
    if (userConfig) {
      console.log("[identity-mw] Parsed userConfig:", {
        id: userConfig.id,
        provider: userConfig.provider,
        model: userConfig.model,
      });
    }
  } catch {
    // Invalid user configuration falls back to the configured model path.
  }

  try {
    const authz = c.req.header("Authorization");
    const token = authz?.startsWith("Bearer ") ? authz.slice(7).trim() : undefined;
    if (token) {
      const payload = await verifyToken(token);
      const sub = typeof payload.sub === "string" && payload.sub ? payload.sub : undefined;
      if (sub) {
        const requestContext = c.get("requestContext") as { set?: (key: string, value: unknown) => void } | undefined;
        if (typeof requestContext?.set === "function") {
          requestContext.set(AUTH_SUB_KEY, sub);
        } else if (!warnedNoRequestContext) {
          warnedNoRequestContext = true;
          console.warn("[identity-mw] requestContext unavailable; authenticated scope was not injected");
        }
      }
    }
  } catch (error) {
    const code = (error as { code?: unknown } | null)?.code;
    if (code === "ERR_JWS_SIGNATURE_VERIFICATION_FAILED" && !warnedAuthSignature) {
      warnedAuthSignature = true;
      console.warn("[identity-mw] Bearer signature verification failed; check JWT_SECRET");
    }
  }

  if (userConfig) {
    await userLLMStore.run(userConfig, next);
  } else {
    await next();
  }
};

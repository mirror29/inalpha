import { clearChatInvocation, registerChatInvocation } from "./chat-invocation-scope.js";
import { randomUUID } from "node:crypto";
import { verifyToken } from "../../auth.js";
import type { InputProcessor, OutputProcessor } from "@mastra/core/processors";
import { AUTH_SUB_KEY } from "../../hooks/with-hooks.js";
import { normalizeChatUsage } from "./chat-usage.js";
import type { ChatCallIdentity, ChatUsageStore } from "./chat-usage-store.js";
import { buildEvolutionLLMSnapshot, type EvolutionLLMSnapshot } from "./evolution-snapshot.js";
import { userLLMStore } from "./provider.js";

export const CHAT_INVOCATION_ID_KEY = "inalpha__chatInvocationId";

/**
 * Registers the same processor in input and output lists so Mastra shares its state.
 * Each input step starts a distinct unknown call before network access. Missing output
 * stays unknown; failed settlement preserves the reply and the earlier unknown row.
 */
export function createChatUsageProcessor(store: ChatUsageStore): InputProcessor & OutputProcessor {
  return {
    id: "chat-usage-receipts",
    async processInputStep({ state, stepNumber, requestContext, modelSettings }) {
      let source: "chat" | "service" = "chat";
      let authSub = requestContext?.get(AUTH_SUB_KEY);
      const serviceToken = requestContext?.get("authToken");
      if (authSub === undefined && typeof serviceToken === "string") {
        authSub = (await verifyToken(serviceToken)).sub;
        source = "service";
      }
      if (typeof authSub !== "string" || !authSub.trim()) {
        throw new Error("chat usage requires authenticated owner");
      }
      const invocationId = typeof state.invocationId === "string" ? state.invocationId : randomUUID();
      state.invocationId = invocationId;
      requestContext!.set(CHAT_INVOCATION_ID_KEY, invocationId);
      const config = userLLMStore.getStore();
      let snapshot: EvolutionLLMSnapshot | undefined;
      if (config) {
        try { snapshot = buildEvolutionLLMSnapshot(config); } catch {
          /** Unsupported chat models retain token evidence with unknown monetary cost. */
        }
      }
      const call: ChatCallIdentity = {
        callId: randomUUID(), invocationId, authSub, stepNumber, source,
        provider: snapshot?.provider ?? config?.provider ?? null,
        model: snapshot?.model ?? config?.model ?? null,
        configId: snapshot?.config_id ?? config?.id ?? null,
        ...(snapshot ? { pricing: { ...snapshot.pricing } } : {}),
      };
      clearChatInvocation(requestContext!);
      await store.begin(call);
      state.activeCall = call;
      if (source === "chat") registerChatInvocation(requestContext!, invocationId, authSub);
      return { modelSettings: { ...modelSettings, maxRetries: 0 } };
    },
    async processOutputStep({ state, stepNumber, usage, messages }) {
      const call = state.activeCall as ChatCallIdentity | undefined;
      if (!call || call.stepNumber !== stepNumber) throw new Error("chat usage call identity unavailable");
      try {
        await store.settle(call, normalizeChatUsage(usage, call.pricing));
      } catch {
        console.warn("[chat-usage] settlement unavailable; call remains unknown");
      }
      return messages;
    },
  };
}

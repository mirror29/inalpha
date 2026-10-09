/** Narrow a record without trusting the transport JSON shape. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Send only the current turn to the persisted orchestrator, preserving the UI archive.
 * Mastra loads its owner-scoped thread memory itself. Keep the latest user message
 * and all following assistant/tool messages together so approval/tool continuations
 * retain their call IDs and results. Discovery and unknown transports pass through.
 */
export function compactChatRequest(init?: RequestInit): RequestInit | undefined {
  if (typeof init?.body !== "string") return init;
  try {
    const envelope: unknown = JSON.parse(init.body);
    if (!isRecord(envelope)
      || !["agent/run", "agent/connect"].includes(String(envelope.method))
      || !isRecord(envelope.params) || envelope.params.agentId !== "orchestrator"
      || !isRecord(envelope.body) || typeof envelope.body.threadId !== "string"
      || !envelope.body.threadId || !Array.isArray(envelope.body.messages)) return init;
    const messages: unknown[] = envelope.body.messages;
    let start = -1;
    for (let index = messages.length - 1; index >= 0; index--) {
      const message = messages[index];
      if (isRecord(message) && message.role === "user") { start = index; break; }
    }
    if (start <= 0) return init;
    return {
      ...init,
      body: JSON.stringify({
        ...envelope,
        body: { ...envelope.body, messages: messages.slice(start) },
      }),
    };
  } catch {
    return init;
  }
}

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
    /** Extend the turn for interleaved approval results; never emit an orphan tool. */
    for (let index = messages.length - 1; index >= start && start > 0; index--) {
      const message = messages[index];
      if (!isRecord(message) || message.role !== "tool") continue;
      let declaringCall = -1;
      for (let callIndex = index - 1; callIndex >= 0; callIndex--) {
        const call = messages[callIndex];
        if (isRecord(call) && call.role === "assistant" && Array.isArray(call.toolCalls)
          && call.toolCalls.some((tool) => isRecord(tool) && tool.id === message.toolCallId)) {
          declaringCall = callIndex;
          break;
        }
      }
      if (declaringCall < 0) return init;
      if (declaringCall < start) {
        start = declaringCall;
        while (start > 0) {
          const preceding = messages[start];
          if (isRecord(preceding) && preceding.role === "user") break;
          start--;
        }
      }
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

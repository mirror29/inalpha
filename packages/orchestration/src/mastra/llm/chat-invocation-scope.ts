/** In-process provenance cannot be forged by client RequestContext fields. */
const scopes = new WeakMap<object, { invocationId: string; authSub: string }>();

/** Registers an owner-bound chat invocation only after its initial receipt persists. */
export function registerChatInvocation(context: object, invocationId: string, authSub: string): void {
  scopes.set(context, { invocationId, authSub });
}

/** Returns only provenance created in this process for the same verified owner. */
export function trustedChatInvocation(context: unknown, authSub: string): string | undefined {
  if (!context || typeof context !== "object") return undefined;
  const scope = scopes.get(context);
  return scope?.authSub === authSub ? scope.invocationId : undefined;
}

/** Clears earlier provenance before another request attempts receipt persistence. */
export function clearChatInvocation(context: object): void {
  scopes.delete(context);
}

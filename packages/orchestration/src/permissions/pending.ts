import { createHash, randomUUID } from "node:crypto";

import {
  claimEvolutionOperation,
  findEvolutionOperation,
  insertPending,
  markResolved,
  rememberEvolutionOperation,
} from "./repo.js";

export type PendingDecision = "allow" | "deny";

export interface PendingApprovalView {
  requestId: string;
  toolName: string;
  toolInput: unknown;
  sessionId: string;
  inputDigest: string;
  createdAt: string;
  deadline: string;
}

export interface PendingRequestArgs {
  toolName: string;
  toolInput: unknown;
  approvalInput: unknown;
  sessionId: string;
  authSub: string;
  timeoutMs?: number;
}

export interface PendingConsumeArgs {
  toolName: string;
  approvalInput: unknown;
  sessionId: string;
  authSub: string;
  reuseAfterConsume?: boolean;
  reuseOnceAfterConsumeMs?: number;
}

interface PendingApprovalRecord extends PendingApprovalView {
  authSub: string;
  status: "pending" | "approved";
  timer: ReturnType<typeof setTimeout>;
}

interface ConsumedApprovalRecord {
  operationId: string;
  expiresAt: string;
  timer: ReturnType<typeof setTimeout>;
  oneShot?: boolean;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const EVOLUTION_OPERATION_RETENTION_MS = 24 * 60 * 60 * 1_000;

export type PendingTelemetrySink = (record: Record<string, unknown>) => void;

const defaultPendingTelemetrySink: PendingTelemetrySink = (record) => {
  console.log(JSON.stringify(record));
};

export interface ApprovalPersistence {
  insertPending(view: PendingApprovalView, authSub?: string): Promise<void>;
  markResolved(
    requestId: string,
    decision: PendingDecision,
    via: "user" | "timeout",
  ): Promise<void>;
  rememberEvolutionOperation(
    args: EvolutionOperationScope & { operationId: string; retentionMs?: number },
  ): Promise<{
    expiresAt: string;
  } | undefined>;
  findEvolutionOperation(args: EvolutionOperationScope): Promise<{
    operationId: string;
    expiresAt: string;
  } | undefined>;
  claimEvolutionOperation?(args: EvolutionOperationScope): Promise<{
    operationId: string;
    expiresAt: string;
  } | undefined>;
}

export interface EvolutionOperationScope {
  authSub: string;
  sessionId: string;
  toolName: string;
  inputDigest: string;
}

/** Produces a deterministic SHA-256 digest for an approval-defining input. */
export function approvalInputDigest(input: unknown): string {
  return createHash("sha256").update(stableStringify(input)).digest("hex");
}

/** Stores pending and approved decisions until one matching tool call consumes them. */
export class PendingApprovalsStore {
  private readonly records = new Map<string, PendingApprovalRecord>();
  private readonly identityIndex = new Map<string, string>();
  private readonly consumedByIdentity = new Map<string, ConsumedApprovalRecord>();
  private readonly consumingByIdentity = new Map<string, Promise<string | undefined>>();
  private readonly telemetry: PendingTelemetrySink;
  private readonly persistence?: ApprovalPersistence;

  constructor(telemetry?: PendingTelemetrySink, persistence?: ApprovalPersistence) {
    this.telemetry = telemetry ?? defaultPendingTelemetrySink;
    this.persistence = persistence;
  }

  /** Registers one owner-bound approval request, deduplicating identical active requests. */
  request(args: PendingRequestArgs): PendingApprovalView {
    const identity = this.identityFor(args);
    const existingId = this.identityIndex.get(identity);
    const existing = existingId ? this.records.get(existingId) : undefined;
    if (existing) return this.toView(existing);

    const requestId = randomUUID();
    const timeoutMs = args.timeoutMs && args.timeoutMs > 0 ? args.timeoutMs : DEFAULT_TIMEOUT_MS;
    const createdAt = new Date();
    const record: PendingApprovalRecord = {
      requestId,
      toolName: args.toolName,
      toolInput: args.toolInput,
      sessionId: args.sessionId,
      authSub: args.authSub,
      inputDigest: approvalInputDigest(args.approvalInput),
      status: "pending",
      createdAt: createdAt.toISOString(),
      deadline: new Date(createdAt.getTime() + timeoutMs).toISOString(),
      timer: setTimeout(() => this.expire(requestId), timeoutMs),
    };
    this.records.set(requestId, record);
    this.identityIndex.set(identity, requestId);
    this.telemetry({
      event: "ask_pending_requested",
      requestId,
      toolName: args.toolName,
      sessionId: args.sessionId,
      authSub: args.authSub,
      inputDigest: record.inputDigest,
      timeoutMs,
      ts: record.createdAt,
    });
    this.persist((p) => p.insertPending(this.toView(record), args.authSub));
    return this.toView(record);
  }

  /** Lists only pending requests owned by the authenticated subject. */
  list(authSub: string): PendingApprovalView[] {
    return Array.from(this.records.values())
      .filter((record) => record.authSub === authSub && record.status === "pending")
      .map((record) => this.toView(record));
  }

  /** Applies an explicit owner-authenticated decision; approved records remain consumable once. */
  respond(requestId: string, decision: PendingDecision, authSub: string): boolean {
    const record = this.records.get(requestId);
    if (!record || record.authSub !== authSub || record.status !== "pending") return false;

    this.telemetry({
      event: "ask_pending_resolved",
      requestId,
      toolName: record.toolName,
      sessionId: record.sessionId,
      authSub,
      decision,
      via: "user",
      ts: new Date().toISOString(),
    });
    this.persist((p) => p.markResolved(requestId, decision, "user"));
    if (decision === "allow") {
      record.status = "approved";
    } else {
      this.remove(record);
    }
    return true;
  }

  /** Atomically consumes one approved decision and returns its restart-stable operation ID. */
  async consumeApproved(args: PendingConsumeArgs): Promise<string | undefined> {
    const identity = this.identityFor(args);
    const inFlight = this.consumingByIdentity.get(identity);
    if (inFlight) {
      await inFlight;
      return await this.consumeApproved(args);
    }

    const run = this.consumeApprovedUnlocked(args, identity);
    this.consumingByIdentity.set(identity, run);
    try {
      return await run;
    } finally {
      if (this.consumingByIdentity.get(identity) === run) {
        this.consumingByIdentity.delete(identity);
      }
    }
  }

  private async consumeApprovedUnlocked(
    args: PendingConsumeArgs,
    identity: string,
  ): Promise<string | undefined> {
    const scope = this.operationScope(args);
    const boundedRetryMs =
      args.reuseOnceAfterConsumeMs && args.reuseOnceAfterConsumeMs > 0
        ? args.reuseOnceAfterConsumeMs
        : undefined;

    if (boundedRetryMs !== undefined) {
      const consumed = this.consumedByIdentity.get(identity);
      if (consumed) {
        if (Date.now() >= Date.parse(consumed.expiresAt)) {
          this.removeConsumed(identity);
        } else if (consumed.oneShot) {
          const operationId = consumed.operationId;
          this.removeConsumed(identity);
          this.telemetry({
            event: "ask_approval_operation_reused",
            requestId: operationId,
            toolName: args.toolName,
            sessionId: args.sessionId,
            authSub: args.authSub,
            ts: new Date().toISOString(),
          });
          return operationId;
        }
      }
      if (this.persistence?.claimEvolutionOperation) {
        const claimed = await this.persistence.claimEvolutionOperation(scope);
        if (claimed && Date.now() < Date.parse(claimed.expiresAt)) {
          this.telemetry({
            event: "ask_approval_operation_recovered",
            requestId: claimed.operationId,
            toolName: args.toolName,
            sessionId: args.sessionId,
            authSub: args.authSub,
            ts: new Date().toISOString(),
          });
          return claimed.operationId;
        }
      }
    } else if (args.reuseAfterConsume) {
      const consumed = this.consumedByIdentity.get(identity);
      if (consumed) {
        if (Date.now() >= Date.parse(consumed.expiresAt)) {
          this.removeConsumed(identity);
        } else {
          this.telemetry({
            event: "ask_approval_operation_reused",
            requestId: consumed.operationId,
            toolName: args.toolName,
            sessionId: args.sessionId,
            authSub: args.authSub,
            ts: new Date().toISOString(),
          });
          return consumed.operationId;
        }
      }
      if (this.persistence) {
        const persisted = await this.persistence.findEvolutionOperation(scope);
        if (persisted && Date.now() < Date.parse(persisted.expiresAt)) {
          this.cacheConsumed(identity, persisted);
          this.telemetry({
            event: "ask_approval_operation_recovered",
            requestId: persisted.operationId,
            toolName: args.toolName,
            sessionId: args.sessionId,
            authSub: args.authSub,
            ts: new Date().toISOString(),
          });
          return persisted.operationId;
        }
      }
    }
    const requestId = this.identityIndex.get(identity);
    const record = requestId ? this.records.get(requestId) : undefined;
    if (!record || record.status !== "approved") return undefined;
    if (Date.now() >= Date.parse(record.deadline)) {
      this.expire(record.requestId);
      return undefined;
    }
    let reusable: { operationId: string; expiresAt: string; oneShot?: boolean } | undefined;
    const shouldReuse = args.reuseAfterConsume || boundedRetryMs !== undefined;
    let persistedReusable = false;
    if (shouldReuse) {
      const retentionMs = boundedRetryMs ?? EVOLUTION_OPERATION_RETENTION_MS;
      const fallback = {
        operationId: record.requestId,
        expiresAt: new Date(Date.now() + retentionMs).toISOString(),
        oneShot: boundedRetryMs !== undefined,
      };
      try {
        const persisted = await this.persistence?.rememberEvolutionOperation({
          ...scope,
          operationId: record.requestId,
          retentionMs,
        });
        persistedReusable = Boolean(persisted);
        reusable = persisted
          ? {
              operationId: record.requestId,
              expiresAt: persisted.expiresAt,
              oneShot: boundedRetryMs !== undefined,
            }
          : fallback;
      } catch (error) {
        this.telemetry({
          event: "ask_approval_operation_persist_failed",
          requestId: record.requestId,
          toolName: record.toolName,
          sessionId: record.sessionId,
          authSub: record.authSub,
          error: error instanceof Error ? error.message : String(error),
          ts: new Date().toISOString(),
        });
        return undefined;
      }
    }
    this.remove(record);
    if (reusable && !(boundedRetryMs !== undefined && persistedReusable)) {
      this.cacheConsumed(identity, reusable);
    }
    this.telemetry({
      event: "ask_approval_consumed",
      requestId: record.requestId,
      toolName: record.toolName,
      sessionId: record.sessionId,
      authSub: record.authSub,
      inputDigest: record.inputDigest,
      ts: new Date().toISOString(),
    });
    return record.requestId;
  }

  size(): number {
    return this.records.size;
  }

  /** Revokes every active record during tests or shutdown. */
  clearAll(reason: PendingDecision = "deny"): void {
    for (const record of Array.from(this.records.values())) {
      this.remove(record);
      if (record.status === "pending") {
        this.persist((p) => p.markResolved(record.requestId, reason, "user"));
      }
    }
    for (const identity of Array.from(this.consumedByIdentity.keys())) {
      this.removeConsumed(identity);
    }
  }

  private expire(requestId: string): void {
    const record = this.records.get(requestId);
    if (!record) return;
    this.remove(record);
    this.telemetry({
      event: "ask_pending_resolved",
      requestId,
      toolName: record.toolName,
      sessionId: record.sessionId,
      authSub: record.authSub,
      decision: "deny",
      via: "timeout",
      ts: new Date().toISOString(),
    });
    if (record.status === "pending") {
      this.persist((p) => p.markResolved(requestId, "deny", "timeout"));
    }
  }

  private remove(record: PendingApprovalRecord): void {
    clearTimeout(record.timer);
    this.records.delete(record.requestId);
    this.identityIndex.delete(
      this.identityKey(record.authSub, record.sessionId, record.toolName, record.inputDigest),
    );
  }

  private removeConsumed(identity: string): void {
    const record = this.consumedByIdentity.get(identity);
    if (!record) return;
    clearTimeout(record.timer);
    this.consumedByIdentity.delete(identity);
  }

  private cacheConsumed(
    identity: string,
    record: { operationId: string; expiresAt: string; oneShot?: boolean },
  ): void {
    const timer = setTimeout(
      () => this.removeConsumed(identity),
      Math.max(Date.parse(record.expiresAt) - Date.now(), 0),
    );
    timer.unref?.();
    this.consumedByIdentity.set(identity, { ...record, timer });
  }

  private operationScope(args: PendingConsumeArgs): EvolutionOperationScope {
    return {
      authSub: args.authSub,
      sessionId: args.sessionId,
      toolName: args.toolName,
      inputDigest: approvalInputDigest(args.approvalInput),
    };
  }

  private identityFor(args: PendingConsumeArgs): string {
    return this.identityKey(
      args.authSub,
      args.sessionId,
      args.toolName,
      approvalInputDigest(args.approvalInput),
    );
  }

  private identityKey(
    authSub: string,
    sessionId: string,
    toolName: string,
    inputDigest: string,
  ): string {
    return `${authSub}\u0000${sessionId}\u0000${toolName}\u0000${inputDigest}`;
  }

  private toView(record: PendingApprovalRecord): PendingApprovalView {
    const { requestId, toolName, toolInput, sessionId, inputDigest, createdAt, deadline } = record;
    return { requestId, toolName, toolInput, sessionId, inputDigest, createdAt, deadline };
  }

  private persist(fn: (persistence: ApprovalPersistence) => Promise<void>): void {
    if (!this.persistence) return;
    try {
      void fn(this.persistence).catch((error) => {
        console.error("[pending] 审批审计落库失败（审批流不受影响）:", error);
      });
    } catch (error) {
      console.error("[pending] 审批审计落库失败（审批流不受影响）:", error);
    }
  }
}

function stableStringify(value: unknown): string {
  if (value === undefined) return "undefined";
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(object[key])}`)
    .join(",")}}`;
}

export const pendingApprovals = new PendingApprovalsStore(undefined, {
  insertPending,
  markResolved,
  rememberEvolutionOperation,
  findEvolutionOperation,
  claimEvolutionOperation,
});

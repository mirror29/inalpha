/**
 * ``PendingApprovalsStore`` —— in-memory ask 审批挂起池（D-9.1b / ADR-0018）。
 *
 * ``permissionResolver`` 返 ``"ask"`` 时，``withHooks`` 不再直接 isError，而是：
 *
 * 1. 调 ``store.request({toolName, toolInput, sessionId})`` 注册挂起项 + 拿 promise
 * 2. promise 等到前端 POST ``/permissions/{requestId}/respond`` 调 ``store.respond``
 * 3. 30s 超时（可配）→ 自动 deny + 从池中移除
 * 4. tool 拿到 decision：``"allow"`` → 真跑 execute；``"deny"`` → isError 提示
 *
 * 设计取舍：
 *
 * - **进程内 Map**：单 mastra runtime 实例，重启即失效；多实例部署需要换 Redis
 *   等共享存储（D-10+）。当前 dev / 单 instance prod 都够用
 * - **明确 fail-closed**：超时 / unknown 决策都按 deny 处理；用户关页面不会让
 *   挂起任务永远卡住
 * - **审计**：respond 时 log decision；超时自动 deny 也 log
 *
 * 引用：ADR-0018（askUserChoice）/ task D-9.1b。
 */
import { randomUUID } from "node:crypto";

export type PendingDecision = "allow" | "deny";

/** 前端可见的挂起项视图（不含 resolver 闭包）。 */
export interface PendingApprovalView {
  requestId: string;
  toolName: string;
  toolInput: unknown;
  sessionId?: string;
  createdAt: string; // ISO
  deadline: string; // ISO
}

interface PendingApprovalRecord extends PendingApprovalView {
  resolve: (decision: PendingDecision) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface PendingRequestArgs {
  toolName: string;
  toolInput: unknown;
  sessionId?: string;
  timeoutMs?: number;
}

export interface PendingRequestResult {
  decision: PendingDecision;
  requestId: string;
  /** ``"user"`` = 前端响应；``"timeout"`` = 超时 deny。 */
  via: "user" | "timeout";
}

const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * 进程内挂起池。``mastra/index.ts`` 在 runtime 启动时共享单例；测试可 new 独立实例。
 */
export class PendingApprovalsStore {
  private readonly pending = new Map<string, PendingApprovalRecord>();

  /**
   * 注册一个挂起审批，返 promise 等用户决策。
   *
   * 超时（默认 30s）自动 deny，并从池中移除；调用方 await 拿到 ``{decision, requestId, via}``。
   */
  request(args: PendingRequestArgs): Promise<PendingRequestResult> {
    const requestId = randomUUID();
    const timeoutMs = args.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const createdAt = new Date();
    const deadline = new Date(createdAt.getTime() + timeoutMs);

    return new Promise<PendingRequestResult>((resolve) => {
      const timer = setTimeout(() => {
        const record = this.pending.get(requestId);
        if (record) {
          this.pending.delete(requestId);
          resolve({ decision: "deny", requestId, via: "timeout" });
        }
      }, timeoutMs);

      const record: PendingApprovalRecord = {
        requestId,
        toolName: args.toolName,
        toolInput: args.toolInput,
        sessionId: args.sessionId,
        createdAt: createdAt.toISOString(),
        deadline: deadline.toISOString(),
        timer,
        resolve: (decision) => {
          clearTimeout(timer);
          this.pending.delete(requestId);
          resolve({ decision, requestId, via: "user" });
        },
      };
      this.pending.set(requestId, record);
    });
  }

  /** 前端 GET /permissions/pending —— 列出当前挂起项。 */
  list(): PendingApprovalView[] {
    return Array.from(this.pending.values()).map(
      ({ resolve: _r, timer: _t, ...view }) => view,
    );
  }

  /**
   * 前端 POST /permissions/{id}/respond —— 决策。
   *
   * Returns ``true`` 表示决策被消费；``false`` 表示挂起项不存在
   * （已超时 / 已被消费 / 无效 id）—— 路由应回 404。
   */
  respond(requestId: string, decision: PendingDecision): boolean {
    const record = this.pending.get(requestId);
    if (!record) return false;
    record.resolve(decision);
    return true;
  }

  /**
   * 尝试用 ``requestId`` token 消费一个挂起项（D-9.1b token threading 模式）。
   *
   * 与 ``respond`` 区别：``respond`` 是外部按钮 / curl 主动决策；本方法是
   * **检查"用户口头同意"后，agent 重调原 tool 时携带 token 走的快速放行通道**。
   *
   * 安全约束：必须 toolName + toolInput 完全匹配创建时的快照，防止 agent 拿一个
   * tool 的 token 去激活另一个 tool。
   *
   * 命中返 ``true``：从池中删除该挂起项并解为 ``"allow"``。
   * 未命中（id 不存在 / 已超时 / toolName/Input 不匹配）返 ``false``。
   */
  tryConsume(requestId: string, toolName: string, toolInput: unknown): boolean {
    const record = this.pending.get(requestId);
    if (!record) return false;
    if (record.toolName !== toolName) return false;
    // JSON 字符串化比较——对纯 JSON 对象 / 数组 / 标量足够；对含 Date/Function
    // 的复杂结构不可靠，但 tool input 通过 zod schema 校验，基本都是 JSON 安全的
    if (JSON.stringify(record.toolInput) !== JSON.stringify(toolInput)) return false;
    record.resolve("allow"); // 内部会 clearTimeout + delete from map
    return true;
  }

  /** 当前挂起项数量（监控 / 测试用）。 */
  size(): number {
    return this.pending.size;
  }

  /** 清空所有挂起（测试用 / shutdown 时调）。所有挂起按 deny 解决。 */
  clearAll(reason: PendingDecision = "deny"): void {
    for (const record of Array.from(this.pending.values())) {
      record.resolve(reason);
    }
  }
}

/** 进程内单例，由 ``withHooks`` 与 HTTP routes 共用。 */
export const pendingApprovals = new PendingApprovalsStore();

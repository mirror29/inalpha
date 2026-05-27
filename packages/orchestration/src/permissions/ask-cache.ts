/**
 * ``AskApprovalCache`` —— ask 路径的 session-scoped 短期通行池（D-9.1b 修订）。
 *
 * 解决"第一次 ask 后用户在 chat 里说'允许'，第二次重调还是被拦"的死循环：
 *
 * 1. 第一次 ``permissionResolver=ask`` 命中 → with-hooks 返 ``requiresApproval`` 错误
 *    + 调 ``mark(sessionId, toolName, input)`` 记一笔
 * 2. Agent 在 chat 里向用户说明 + 等用户口头同意
 * 3. 用户回 "允许 / 同意" → agent 重调同一个 tool 同一份 input
 * 4. 第二次 ask 命中 → with-hooks 先调 ``consume(sessionId, toolName, input)`` ——
 *    若命中（同 session 60s 内有 mark）→ 一次性消费 + 放行；未命中 → 走第 1 步
 *
 * 安全模型（接受性的设计取舍）：
 *
 * - sessionId 是 *agent loop 范围内*（Mastra thread / run id），不跨会话；A 用户
 *   的允许不会被 B 用户复用
 * - 60s TTL：足够给 agent 在 chat 转一圈，短到防 agent 长期"残留许可"复用
 * - 一次性：消费即删；agent 想再做同样动作必须再走一轮 ask
 * - **agent 是否真等了用户回复完全靠 prompt 纪律 + 后端硬校验作护栏**：
 *   理论上 agent 可以第一次 ask 失败后立刻重试（不等用户）→ 也会被允许。
 *   这就是为什么 promote_candidate 等危险操作的**后端**还做 ``fitness > baseline``
 *   等硬校验作第二道防线；本缓存只解决 UX 死循环，不充当强制审批门
 */

interface AskCacheEntry {
  markedAt: number;
  /** TTL 到期自动清理的 timer，consume 时记得 clearTimeout */
  timer: ReturnType<typeof setTimeout>;
}

const DEFAULT_TTL_MS = 60_000;

export class AskApprovalCache {
  private readonly entries = new Map<string, AskCacheEntry>();
  private readonly ttlMs: number;

  constructor(ttlMs: number = DEFAULT_TTL_MS) {
    if (ttlMs <= 0) throw new Error(`ttlMs must be positive, got ${ttlMs}`);
    this.ttlMs = ttlMs;
  }

  /** 拼 cache key。无 sessionId 时回退到 ``"__global__"`` —— 单进程 dev 环境够用。 */
  private static keyFor(
    sessionId: string | undefined,
    toolName: string,
    input: unknown,
  ): string {
    const sid = sessionId && sessionId.length > 0 ? sessionId : "__global__";
    // JSON.stringify 对 plain object input 足够；含 Date / undefined 等会丢精度
    // 但 tool input 走过 zod 校验，基本都是 JSON 安全
    return `${sid}::${toolName}::${JSON.stringify(input)}`;
  }

  /**
   * 标记 (sessionId, toolName, input) 已被 ask 过；TTL 后自动失效。
   * 若已存在条目，重置 TTL（连续多次 ask 同一动作不需要重启计时）。
   */
  mark(sessionId: string | undefined, toolName: string, input: unknown): void {
    const key = AskApprovalCache.keyFor(sessionId, toolName, input);
    const existing = this.entries.get(key);
    if (existing) clearTimeout(existing.timer);
    const timer = setTimeout(() => {
      this.entries.delete(key);
    }, this.ttlMs);
    this.entries.set(key, { markedAt: Date.now(), timer });
  }

  /**
   * 检查 + 一次性消费。命中（同 sessionId + toolName + input 60s 内被 mark）→
   * 删除条目 + 返 ``true``；未命中 → 返 ``false``。
   */
  consume(sessionId: string | undefined, toolName: string, input: unknown): boolean {
    const key = AskApprovalCache.keyFor(sessionId, toolName, input);
    const entry = this.entries.get(key);
    if (!entry) return false;
    // 双保险：即便 setTimeout 未触发，超时仍按未命中处理
    if (Date.now() - entry.markedAt > this.ttlMs) {
      clearTimeout(entry.timer);
      this.entries.delete(key);
      return false;
    }
    clearTimeout(entry.timer);
    this.entries.delete(key);
    return true;
  }

  /** 当前活跃条目数（监控 / 测试用）。 */
  size(): number {
    return this.entries.size;
  }

  /** 测试 / shutdown：清空。 */
  clear(): void {
    for (const entry of this.entries.values()) {
      clearTimeout(entry.timer);
    }
    this.entries.clear();
  }
}

/** 进程内单例，被 with-hooks 默认使用。 */
export const defaultAskCache = new AskApprovalCache();

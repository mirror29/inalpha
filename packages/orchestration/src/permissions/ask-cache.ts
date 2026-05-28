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

/**
 * **稳定** JSON stringify —— object keys 按字典序，避免 ``{a,b}`` vs ``{b,a}`` 撞不上。
 *
 * 必须用 stable 形式：DeepSeek / GPT 生成 tool call JSON 时 key 顺序在两次调用之间
 * 经常变（实测 ``{candidateId, reason}`` ↔ ``{reason, candidateId}``），plain
 * ``JSON.stringify`` 会给两个不同字符串 → cache miss → 死循环。
 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`).join(",")}}`;
}

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
    return `${sid}::${toolName}::${stableStringify(input)}`;
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
   *
   * 未命中时若 ``debugSink`` 提供且存在"同 sessionId + 同 toolName 但 input 不同"
   * 的条目，会调 sink 打 mismatch diff（帮 user 定位是 LLM 改了 input 哪个字段
   * 导致 cache 撞不上）。
   */
  consume(
    sessionId: string | undefined,
    toolName: string,
    input: unknown,
    debugSink?: (msg: string) => void,
  ): boolean {
    const key = AskApprovalCache.keyFor(sessionId, toolName, input);
    const entry = this.entries.get(key);
    if (!entry) {
      if (debugSink) this.debugWhyMiss(sessionId, toolName, input, debugSink);
      return false;
    }
    // 双保险：即便 setTimeout 未触发，超时仍按未命中处理
    if (Date.now() - entry.markedAt > this.ttlMs) {
      clearTimeout(entry.timer);
      this.entries.delete(key);
      if (debugSink) debugSink(`AskCache: entry expired for ${toolName} (sid=${sessionId})`);
      return false;
    }
    clearTimeout(entry.timer);
    this.entries.delete(key);
    return true;
  }

  /** 未命中时尝试找同 sid+tool 但 input 不同的条目，打 diff 帮排查。 */
  private debugWhyMiss(
    sessionId: string | undefined,
    toolName: string,
    input: unknown,
    sink: (msg: string) => void,
  ): void {
    const sid = sessionId && sessionId.length > 0 ? sessionId : "__global__";
    const prefix = `${sid}::${toolName}::`;
    const candidates: string[] = [];
    for (const k of this.entries.keys()) {
      if (k.startsWith(prefix)) candidates.push(k.slice(prefix.length));
    }
    if (candidates.length === 0) {
      sink(
        `AskCache miss: no prior mark for sid=${sid} tool=${toolName} ` +
          `(possible cause: different sessionId between calls / first call wasn't ask)`,
      );
      return;
    }
    const got = stableStringify(input);
    sink(
      `AskCache miss: sid=${sid} tool=${toolName} input mismatch.\n` +
        `  retry sent: ${got}\n` +
        `  prior had: ${candidates.join(" | ")}`,
    );
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

/**
 * Shared Memory —— orchestrator / trader / risk 共用一份。
 *
 * D-8a 起步形态：本地 SQLite 文件 ``.mastra/inalpha-memory.db``，gitignored。
 *
 * **共用一份的设计取舍**：
 *
 * - ✅ supervisor 调 subagent 时上下文可贯通，trader 看得见 orchestrator 当前 thread
 *   的 planId（同 thread）
 * - ✅ playground 重启 / mastra dev hot reload 不丢历史
 * - ⚠️  D-8b 持久化时考虑 Postgres + 按 user 隔离 resourceId
 *
 * ⚠️ **多用户隔离硬约束**（D-8b' review B11 / 高风险 #C）
 *
 * Mastra ``Memory`` 在多用户场景下**必须**靠 caller 传 ``{ resourceId, threadId }``
 * 双 ID 来隔离：
 *
 * - ``resourceId``：业务级隔离 ID，**应该等于 JWT.sub**（用户身份）
 * - ``threadId``：会话级 ID，由前端 / 调用方分配，**不要在不同用户间复用**
 *
 * 如果 caller 漏传 ``resourceId``，Mastra 默认会用空字符串 → **不同用户共享同一空
 * 桶** → 历史互窜 + 隐私泄漏。
 *
 * 上 prod 前必须：
 *
 * 1. 在 agent.generate / stream 入口（apps/web Next.js API route）强制
 *    ``assertScopedRequest({ resourceId, threadId })``
 * 2. ``resourceId`` 从 session.user.id 派生，**不能**让客户端任意传
 * 3. LibSQLStore 换成 PostgresStore 走与业务表同一 PG（按 user_id 分区）
 *
 * 改成多实例 / 隔离的触发条件：上多用户 / live trading 时按 ADR-0011 §isolation 拆。
 */
import { LibSQLStore } from "@mastra/libsql";
import { Memory } from "@mastra/memory";

import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

// 把 db 文件放在 package 根的 .mastra/ 下（已经在 .gitignore）
const dbDir = resolve(process.cwd(), ".mastra");
if (!existsSync(dbDir)) {
  mkdirSync(dbDir, { recursive: true });
}
const dbUrl = `file:${dbDir}/inalpha-memory.db`;

export const memoryStore = new LibSQLStore({
  id: "inalpha-memory",
  url: dbUrl,
});

export const sharedMemory = new Memory({
  storage: memoryStore,
  // D-8a 默认配置：保留最近 50 条 history 给 LLM；不开 semanticRecall（要 vector）
  options: {
    lastMessages: 50,
    semanticRecall: false,
    workingMemory: {
      enabled: false,
    },
  },
});

/**
 * 校验 agent.generate / stream 入参带了 ``resourceId`` + ``threadId``。
 *
 * 上 prod 前应在所有 mastra agent 调用入口（apps/web Next.js API route）守一道：
 *
 * ```ts
 * assertScopedRequest({
 *   resourceId: session.user.id,
 *   threadId: body.threadId,
 * });
 * ```
 *
 * D-8b 起 dev 路径不强制（playground 跑测试时往往不传），但**生产路径必须**。
 */
export class MissingScopeError extends Error {
  constructor(missing: readonly string[]) {
    super(
      `mastra request missing scope ids: ${missing.join(", ")} — memory will leak across users`,
    );
    this.name = "MissingScopeError";
  }
}

export function assertScopedRequest(opts: {
  resourceId?: string;
  threadId?: string;
}): asserts opts is { resourceId: string; threadId: string } {
  const missing: string[] = [];
  if (!opts.resourceId || typeof opts.resourceId !== "string") missing.push("resourceId");
  if (!opts.threadId || typeof opts.threadId !== "string") missing.push("threadId");
  if (missing.length > 0) throw new MissingScopeError(missing);
}

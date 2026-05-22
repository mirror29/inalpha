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

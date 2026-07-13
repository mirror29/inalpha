/**
 * Dashboard PostgreSQL 连接池。
 *
 * 用于访问 users 表的 preferences 字段（多租户 LLM 配置）。
 *
 * 懒加载 Pool，按 DATABASE_URL 配置（从根 .env 继承）。
 * 进程退出时自动关闭。
 */
// server-only 仅在非测试环境导入
if (process.env.NODE_ENV !== "test") {
  require("server-only");
}

import pg from "pg";

let _pool: pg.Pool | undefined;

/**
 * 获取数据库连接池。
 *
 * 懒加载，首次调用时初始化。
 * DATABASE_URL 从根 .env 继承（next.config.ts 的 loadRootEnv）。
 *
 * @returns PostgreSQL Pool 实例
 */
export function getPool(): pg.Pool {
  if (_pool !== undefined) return _pool;

  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new Error(
      "DATABASE_URL 未配置。请在根目录 .env 填入 PostgreSQL 连接字符串。",
    );
  }

  _pool = new pg.Pool({
    connectionString: databaseUrl,
    max: 4,
  });
  return _pool;
}

/**
 * 关闭连接池。
 *
 * 进程退出时调用，幂等。
 */
export async function closePool(): Promise<void> {
  if (_pool === undefined) return;
  const p = _pool;
  _pool = undefined;
  await p.end();
}

// 进程退出时自动关闭池
if (process.env.NODE_ENV !== "test") {
  process.once("SIGTERM", () => void closePool());
  process.once("SIGINT", () => void closePool());
}
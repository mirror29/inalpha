/**
 * 占卜台的 Postgres 持久层 —— 存「确定性占卜记录」,供独立模块回看历史。
 *
 * 职责：
 *
 * - 懒加载一个 `pg.Pool`(按 DATABASE_URL / settings.databaseUrl)
 * - `insertDivination` / `listDivinations` / `getDivination` —— 无 ORM,纯参数化 SQL
 *
 * 何时用：仅在 `divination/api.ts`(HTTP 端点)使用;agent / tool 业务代码不要从这里读写
 * (会话占卜走 `tools/divination.ts`,不落库)。
 *
 * 何时不用：单测里用 `setPool()` 注入 mock,避免连真实库。
 *
 * 坑：
 *
 * - `reading` 列存完整 `DivinationView`(卦象/牌面 + disclaimer)的 jsonb 快照 ——
 *   引擎确定性,但典籍文案可能随版本演进,落库快照保证历史回看与当时一致。
 * - `subject` = 控制台身份(JWT.sub),做隶属/隔离;查询永远带 subject 过滤。
 */
import { Pool, type PoolConfig } from "pg";

import { getSettings } from "../config.js";

/** 占卜形态 —— 与前端按钮一一对应。 */
export type DivinationMode = "hexagram" | "tarotSingle" | "tarotThree";

/** 一条占卜记录(行映射后的形态)。 */
export interface DivinationRecord {
  id: string;
  subject: string;
  mode: DivinationMode;
  question: string;
  symbol: string | null;
  kind: "hexagram" | "tarot";
  /** 完整 DivinationView 快照(reading + disclaimer);前端直接渲染。 */
  reading: unknown;
  createdAt: Date;
}

let _pool: Pool | undefined;

/** 懒加载 Pool；测试时用 setPool() 覆盖。 */
export function getPool(): Pool {
  if (_pool !== undefined) return _pool;
  const settings = getSettings();
  if (!settings.databaseUrl) {
    throw new Error(
      "DATABASE_URL 未配置 —— 占卜台历史需要 Postgres。请在 .env 里设置 DATABASE_URL。",
    );
  }
  const cfg: PoolConfig = { connectionString: settings.databaseUrl, max: 4 };
  _pool = new Pool(cfg);
  return _pool;
}

/** 测试时显式注入 Pool(或 mock)。 */
export function setPool(pool: Pool | undefined): void {
  _pool = pool;
}

/** 关池：进程退出时调,幂等。 */
export async function closePool(): Promise<void> {
  if (_pool === undefined) return;
  const p = _pool;
  _pool = undefined;
  await p.end();
}

/** 行 → DivinationRecord。 */
function rowToRecord(row: Record<string, unknown>): DivinationRecord {
  return {
    id: row["id"] as string,
    subject: row["subject"] as string,
    mode: row["mode"] as DivinationMode,
    question: row["question"] as string,
    symbol: (row["symbol"] as string | null) ?? null,
    kind: row["kind"] as "hexagram" | "tarot",
    reading: row["reading"],
    createdAt: row["created_at"] as Date,
  };
}

/** 插入一条占卜记录,返回落库后的完整行。 */
export async function insertDivination(input: {
  subject: string;
  mode: DivinationMode;
  question: string;
  symbol: string | null;
  kind: "hexagram" | "tarot";
  reading: unknown;
}): Promise<DivinationRecord> {
  const { rows } = await getPool().query(
    `INSERT INTO divinations (subject, mode, question, symbol, kind, reading)
     VALUES ($1, $2, $3, $4, $5, $6::jsonb)
     RETURNING id, subject, mode, question, symbol, kind, reading, created_at`,
    [
      input.subject,
      input.mode,
      input.question,
      input.symbol,
      input.kind,
      JSON.stringify(input.reading),
    ],
  );
  return rowToRecord(rows[0]);
}

/** 列某 subject 的历史(按时间倒序),limit 截断。 */
export async function listDivinations(
  subject: string,
  limit: number,
): Promise<DivinationRecord[]> {
  const { rows } = await getPool().query(
    `SELECT id, subject, mode, question, symbol, kind, reading, created_at
     FROM divinations WHERE subject = $1
     ORDER BY created_at DESC LIMIT $2`,
    [subject, limit],
  );
  return rows.map(rowToRecord);
}

/** 按 id 查单条(带 subject 隶属校验);不存在 / 不属于该 subject 返回 null。 */
export async function getDivination(
  id: string,
  subject: string,
): Promise<DivinationRecord | null> {
  const { rows } = await getPool().query(
    `SELECT id, subject, mode, question, symbol, kind, reading, created_at
     FROM divinations WHERE id = $1 AND subject = $2`,
    [id, subject],
  );
  return rows[0] ? rowToRecord(rows[0]) : null;
}

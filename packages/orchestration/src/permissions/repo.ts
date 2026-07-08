/**
 * pending_approvals 的 Postgres 持久层 —— ask 审批的**审计历史**（migration 0021）。
 *
 * 职责：
 *
 * - 懒加载 `pg.Pool`（与 scheduler/repo.ts 同款，按 settings.databaseUrl）
 * - 每条审批全生命周期一行：requested → resolved（user/timeout）/ expired_restart
 * - 启动 sweep：把遗留 pending 行批量置 expired_restart（执行上下文随进程死亡，
 *   不可恢复，只落终态保证可见）
 *
 * 何时用：仅 `PendingApprovalsStore`（写）与 permissions HTTP API（读 history）。
 *
 * 何时不用：审批**决策**永远走内存 Promise —— 本层是审计面不是闸门；
 * 单测用 setPool() 注 mock，不连真实库。
 *
 * 坑：
 *
 * - **fail-open**：任何落库失败只 console.error，绝不阻断审批流（闸门语义
 *   fail-closed 在 pending.ts，审计面挂了不能把审批一起拖死）
 * - DATABASE_URL 未配置时本层整体降级为 no-op（dev 无 PG 也能跑）
 */
import { Pool, type PoolConfig } from "pg";

import { getSettings } from "../config.js";
import { maskSensitive } from "../redact.js";
import type { PendingApprovalView, PendingDecision } from "./pending.js";

/** 终态：决策 / 超时 / 重启扫尾。 */
export type ApprovalStatus =
  | "pending"
  | "allowed"
  | "denied"
  | "expired_timeout"
  | "expired_restart";

/** history API 返回的行。 */
export interface ApprovalHistoryRow {
  requestId: string;
  toolName: string;
  toolInput: unknown;
  sessionId: string | null;
  authSub: string | null;
  status: ApprovalStatus;
  via: "user" | "timeout" | "restart" | null;
  createdAt: string; // ISO
  deadline: string; // ISO
  resolvedAt: string | null; // ISO
}

let _pool: Pool | undefined;
let _noDbWarned = false;

/** 懒加载 Pool；DATABASE_URL 未配置返回 null（整层 no-op 降级）。 */
function getPoolOrNull(): Pool | null {
  if (_pool !== undefined) return _pool;
  const settings = getSettings();
  if (!settings.databaseUrl) {
    if (!_noDbWarned) {
      _noDbWarned = true;
      console.warn(
        "[approvals-repo] DATABASE_URL 未配置 —— 审批历史不落库（审批流不受影响）",
      );
    }
    return null;
  }
  const cfg: PoolConfig = { connectionString: settings.databaseUrl, max: 2 };
  _pool = new Pool(cfg);
  return _pool;
}

/** 测试时显式注入 Pool（或 mock）；传 undefined 重置回懒加载。 */
export function setPool(pool: Pool | undefined): void {
  _pool = pool;
  _noDbWarned = false;
}

/** 关池：进程退出时调，幂等。 */
export async function closePool(): Promise<void> {
  if (_pool === undefined) return;
  const p = _pool;
  _pool = undefined;
  await p.end();
}

/**
 * 注册挂起时插一行 status=pending。fail-open：失败只 log。
 *
 * @param authSub Bearer JWT 的 sub（账户主体）。缺失时 auth_sub 落 NULL（老数据 / 本地 dev 无鉴权）。
 */
export async function insertPending(view: PendingApprovalView, authSub?: string): Promise<void> {
  try {
    const pool = getPoolOrNull();
    if (!pool) return;
    await pool.query(
      `INSERT INTO pending_approvals
         (request_id, tool_name, tool_input, session_id, auth_sub, status, created_at, deadline)
       VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7)
       ON CONFLICT (request_id) DO NOTHING`,
      [
        view.requestId,
        view.toolName,
        // 入库前脱敏：与 audit-log 同一套字段名 mask（apiKey/token/secret/PII），
        // 两条持久化路径处理对称，凭据/PII 不以明文落 pending_approvals。
        JSON.stringify(maskSensitive(view.toolInput ?? null)),
        view.sessionId ?? null,
        authSub ?? null,
        view.createdAt,
        view.deadline,
      ],
    );
  } catch (err) {
    console.error("[approvals-repo] insertPending 失败（审批流不受影响）:", err);
  }
}

/** 决策 / 超时落终态。fail-open：失败只 log。 */
export async function markResolved(
  requestId: string,
  decision: PendingDecision,
  via: "user" | "timeout",
): Promise<void> {
  const status: ApprovalStatus =
    via === "timeout" ? "expired_timeout" : decision === "allow" ? "allowed" : "denied";
  try {
    const pool = getPoolOrNull();
    if (!pool) return;
    await pool.query(
      `UPDATE pending_approvals
         SET status = $2, via = $3, resolved_at = now()
       WHERE request_id = $1 AND status = 'pending'`,
      [requestId, status, via],
    );
  } catch (err) {
    console.error("[approvals-repo] markResolved 失败（审批流不受影响）:", err);
  }
}

/**
 * 启动扫尾：上一进程遗留的 pending 行批量置 expired_restart。
 * 返回扫掉的行数（log / 测试用）；DB 不可用返回 0。
 */
export async function sweepStalePending(): Promise<number> {
  try {
    const pool = getPoolOrNull();
    if (!pool) return 0;
    const res = await pool.query(
      `UPDATE pending_approvals
         SET status = 'expired_restart', via = 'restart', resolved_at = now()
       WHERE status = 'pending'`,
    );
    return res.rowCount ?? 0;
  } catch (err) {
    console.error("[approvals-repo] sweepStalePending 失败（启动不受影响）:", err);
    return 0;
  }
}

/**
 * 审批历史（含终态），按创建时间倒序。DB 不可用 / 查询中途断连均返回空数组
 * （与 insertPending / markResolved / sweepStalePending 同 fail-open 约定：repo 层
 *  自吞错误，不把异常抛给 handler，保证调用路径返回形态稳定）。
 *
 * @param authSub 可选账户主体过滤。有值时只返回该 sub 的记录（含 auth_sub=NULL 的旧行兼容）；
 *                无值（undefined）返回全部记录（本地 dev 无鉴权场景）。
 */
export async function listHistory(authSub?: string, limit = 50): Promise<ApprovalHistoryRow[]> {
  try {
    const pool = getPoolOrNull();
    if (!pool) return [];
    const capped = Math.min(Math.max(1, limit), 200);
    let sql: string;
    const params: unknown[] = [capped];
    if (authSub !== undefined) {
      sql =
        `SELECT request_id, tool_name, tool_input, session_id, auth_sub, status, via,
                created_at, deadline, resolved_at
           FROM pending_approvals
          WHERE auth_sub IS NULL OR auth_sub = $2
          ORDER BY created_at DESC
          LIMIT $1`;
      params.push(authSub);
    } else {
      // 无 authSub（本地 dev 无 Bearer token / token 无 sub）→ 返回全部
      sql =
        `SELECT request_id, tool_name, tool_input, session_id, auth_sub, status, via,
                created_at, deadline, resolved_at
           FROM pending_approvals
          ORDER BY created_at DESC
          LIMIT $1`;
    }
    const { rows } = await pool.query(sql, params);
    return rows.map((r) => ({
      requestId: String(r.request_id),
      toolName: String(r.tool_name),
      toolInput: r.tool_input,
      sessionId: r.session_id === null ? null : String(r.session_id),
      authSub: r.auth_sub === null ? null : String(r.auth_sub),
      status: r.status as ApprovalStatus,
      via: (r.via ?? null) as ApprovalHistoryRow["via"],
      createdAt: toIso(r.created_at),
      deadline: toIso(r.deadline),
      resolvedAt: r.resolved_at === null ? null : toIso(r.resolved_at),
    }));
  } catch (err) {
    console.error("[approvals-repo] listHistory 失败（活动流不受影响）:", err);
    return [];
  }
}

function toIso(v: unknown): string {
  return v instanceof Date ? v.toISOString() : String(v);
}

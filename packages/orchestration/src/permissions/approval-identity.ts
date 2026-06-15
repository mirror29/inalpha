/**
 * ``approval-identity`` —— ask 路径的"审批身份投影"（D-9.1b 修订续）。
 *
 * **解决"promote 时被反复要求确认"的根因。**
 *
 * 问题：``AskApprovalCache`` 用 ``stableStringify(整个 input)`` 当 cache key。
 * 而 ``paper.promote_candidate`` 的 input 里带一个 LLM **自由生成**的审计字段
 * ``reason``（``min(20).max(1000)``）。流程是：
 *
 *   1. 第一次 ask → with-hooks ``mark(sessionId, tool, input)`` + 返 requiresApproval
 *   2. agent 在 chat 里说明 → 用户回"允许"
 *   3. agent 重调同 tool —— 但 LLM **几乎不可能逐字复刻**上一段 20~1000 字的
 *      ``reason``（措辞 / 数字表述每次都略有出入）
 *   4. ``stableStringify`` 只统一 key **顺序**、统一不了 value → 新 key 撞不上旧
 *      mark → cache miss → **再弹一次确认**（用户视角就是"确认了好几次"）
 *
 * prompt（orchestrator case 5）+ tool description + ask-cache 注释都在反复叮嘱
 * LLM "重调时 input 要完全一致"，那是在用纪律对抗一个结构问题——治标不治本。
 *
 * 修复：让 tool 声明**哪些 input 字段定义"审批身份"**。cache 的 mark / consume
 * 只比对**投影后的子集**；``reason`` 仍照常进 ``execute``（完整 input 不变），
 * 只是不再参与审批匹配。
 *
 * 安全性（为什么投影不会放松审批）：
 *
 * - 投影到 ``candidateId`` 正是"这次 promote 操作的身份"。同一候选在 60s 内重复
 *   promote 本就该直接放行——后端有 ``status='candidate'`` 幂等护栏（已 promoted
 *   返 409），不会真重复转正。
 * - 不同候选 ``candidateId`` 不同 → 投影后 key 仍不同 → 各自走一轮审批，不串号。
 * - 跨 sessionId 隔离不受影响（key 仍含 sessionId）。
 *
 * 扩展：未来若有别的 ask tool 也带自由文本字段（如 ``risk.update_config`` 的备注），
 * 在 ``APPROVAL_IDENTITY_FIELDS`` 里登记其身份字段即可；未登记的 tool 默认用完整
 * input（向后兼容，行为与改动前一致）。
 */

/**
 * ``toolName`` → 定义"审批身份"的 input 字段白名单。
 *
 * **未在此登记的 tool 用完整 input 当 key**（向后兼容）。只有"input 里混了不影响
 * 操作身份的自由文本 / 易变字段"的 tool 才需要登记。
 */
export const APPROVAL_IDENTITY_FIELDS: Readonly<Record<string, readonly string[]>> = {
  // promote 的身份只看 candidateId；reason 是自由文本审计字段，每次重调措辞会变，
  // 不能让它参与审批匹配，否则用户每"允许"一次 LLM 换个说法又被拦一次。
  "paper.promote_candidate": ["candidateId"],
};

/**
 * 把 tool input 投影成"审批身份子集"，供 ask-cache 做 mark / consume 的 key。
 *
 * - 未登记的 tool / 非 object input → 原样返回（行为不变）
 * - 登记的 tool → 只保留白名单字段（缺失的字段跳过，不补 undefined，避免 key 漂移）
 */
export function projectApprovalInput(toolName: string, input: unknown): unknown {
  const fields = APPROVAL_IDENTITY_FIELDS[toolName];
  if (!fields) return input;
  if (!input || typeof input !== "object" || Array.isArray(input)) return input;
  const obj = input as Record<string, unknown>;
  const projected: Record<string, unknown> = {};
  for (const f of fields) {
    if (f in obj) projected[f] = obj[f];
  }
  // 身份字段全缺(schema 演进使 candidateId 变可选 / 越过 schema 直调)→ 投影为空。
  // 绝不能返回 {}:stableStringify({})="{}" 会成同 session 所有缺身份调用的万能 key,
  // 一次批准放行后续全部。退回完整 input,让每个不同 input 各自走一轮审批(fail-safe)。
  if (Object.keys(projected).length === 0) return input;
  return projected;
}

import "server-only";

import { MastraClient } from "@mastra/client-js";

import { BACKENDS, CONSOLE_SUBJECT, getServiceToken } from "./backend";

/**
 * Mastra memory(会话/线程)读取层 —— **仅 server 侧**。
 *
 * 会话即 mastra Memory 的 thread,按 `{resourceId, threadId}` 隔离(见
 * packages/orchestration/src/mastra/memory.ts)。dashboard 单租户下 resourceId 固定为
 * CONSOLE_SUBJECT。用 `@mastra/client-js` 走 mastra(4111)自动暴露的 memory 路由,
 * 复用 `getServiceToken()` 注 JWT。
 */

const AGENT_ID = "orchestrator";

/** 会话摘要 —— 历史会话列表用。 */
export interface ChatThreadSummary {
  id: string;
  title: string | null;
  createdAt: string;
  updatedAt: string;
}

/** 历史消息(简化为可渲染纯文本)—— 切换会话时回填 UI 用。 */
export interface ChatHistoryMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

async function mastraClient(): Promise<MastraClient> {
  const token = await getServiceToken();
  return new MastraClient({
    baseUrl: BACKENDS.mastra,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** 无标题线程的标题回填上限 —— 拉首条消息有成本,bounded fan-out。 */
const TITLE_BACKFILL_CAP = 8;

/** 正在回填标题的 threadId —— 防并发(两次轮询撞同一批无标题线程)重复 fan-out + 双写。 */
const backfillInflight = new Set<string>();

/**
 * 列出当前 resource 的历史会话(按最近更新倒序)。
 * @param limit 拉取条数上限
 * @param opts.backfillTitles 无标题线程是否拉首条消息回填标题(默认 true)。
 *   历史下拉要展示标题 → true;8s 轮询的活动页有 `#id` 兜底、不需要 → 传 false,
 *   省掉热路径上每 8s 最多 8 次 `listChatMessages` fan-out + 写回。
 */
export async function listChatThreads(
  limit = 50,
  opts: { backfillTitles?: boolean } = {},
): Promise<ChatThreadSummary[]> {
  const { backfillTitles = true } = opts;
  const client = await mastraClient();
  const res = (await client.listMemoryThreads({
    resourceId: CONSOLE_SUBJECT,
    agentId: AGENT_ID,
    perPage: limit,
    orderBy: { field: "updatedAt", direction: "DESC" },
  })) as { threads?: RawThread[] };

  const summaries: ChatThreadSummary[] = (res.threads ?? []).map((t) => ({
    id: t.id,
    title: t.title?.trim() || null,
    createdAt: toIso(t.createdAt),
    updatedAt: toIso(t.updatedAt ?? t.createdAt),
  }));

  if (!backfillTitles) return summaries;

  // 无标题的(老会话 / 标题还没写上)→ 拉首条用户消息当标题,bounded;
  // 跳过正被别的请求回填的线程,防并发重复 fan-out / 双写。
  const titleless = summaries
    .filter((s) => !s.title && !backfillInflight.has(s.id))
    .slice(0, TITLE_BACKFILL_CAP);
  await Promise.allSettled(
    titleless.map(async (s) => {
      backfillInflight.add(s.id);
      try {
        const msgs = await listChatMessages(s.id);
        const first = msgs.find((m) => m.role === "user");
        if (!first) return;
        s.title = first.content.trim().slice(0, 60);
        // 持久化回写,下次轮询直接读 title、不再拉消息(自愈,避免每 8s 重复 fan-out)。
        void setChatThreadTitle(s.id, s.title).catch(() => {});
      } finally {
        backfillInflight.delete(s.id);
      }
    }),
  );

  return summaries;
}

/** 取某会话的历史消息,映射为可渲染纯文本(丢弃 tool/system,仅 user/assistant)。 */
export async function listChatMessages(
  threadId: string,
): Promise<ChatHistoryMessage[]> {
  const client = await mastraClient();
  const res = (await client.listThreadMessages(threadId, {
    agentId: AGENT_ID,
  })) as { messages?: RawMessage[] };
  return (res.messages ?? [])
    .map(mapDbMessage)
    .filter((m): m is ChatHistoryMessage => m !== null);
}

/**
 * 设置会话标题(发起会话首条消息后调用)。
 * 先 update(线程已被 stream 创建的常见情形),不存在则 create —— 两条路都落到带 title 的线程。
 */
export async function setChatThreadTitle(
  threadId: string,
  title: string,
): Promise<void> {
  const client = await mastraClient();
  const clean = title.trim().slice(0, 60);
  if (!clean) return;
  try {
    await client.getMemoryThread({ threadId, agentId: AGENT_ID }).update({
      title: clean,
      metadata: {},
      resourceId: CONSOLE_SUBJECT,
      agentId: AGENT_ID,
    });
  } catch {
    await client.createMemoryThread({
      threadId,
      resourceId: CONSOLE_SUBJECT,
      title: clean,
    } as Parameters<typeof client.createMemoryThread>[0]);
  }
}

// ── 内部:宽松解析 mastra 返回(版本演进容差)──

interface RawThread {
  id: string;
  title?: string | null;
  createdAt?: string | Date;
  updatedAt?: string | Date;
}

interface RawMessage {
  id?: string;
  role?: string;
  content?: unknown;
  createdAt?: string | Date;
}

function toIso(v: string | Date | undefined): string {
  if (!v) return "";
  return v instanceof Date ? v.toISOString() : String(v);
}

/** MastraDBMessage.content 可能是 string,或 {parts:[{type,text}]},或多模态数组。 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  const parts =
    content && typeof content === "object" && "parts" in content
      ? (content as { parts?: unknown[] }).parts
      : Array.isArray(content)
        ? content
        : undefined;
  if (!Array.isArray(parts)) return "";
  return parts
    .map((p) =>
      p && typeof p === "object" && "type" in p && p.type === "text"
        ? String((p as { text?: unknown }).text ?? "")
        : "",
    )
    .join("");
}

function mapDbMessage(m: RawMessage): ChatHistoryMessage | null {
  if (m.role !== "user" && m.role !== "assistant") return null;
  const content = extractText(m.content);
  if (!content.trim()) return null;
  return { id: m.id ?? crypto.randomUUID(), role: m.role, content };
}

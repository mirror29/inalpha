import "server-only";

import { MastraClient } from "@mastra/client-js";

import { BACKENDS, CONSOLE_SUBJECT, getServiceToken } from "./backend";
import { stripPageContext } from "./page-context-shared";

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

/**
 * 历史消息 —— 切换会话时回填 UI 用,形状对齐前端 live 的 AG 消息模型
 * （assistant 带 `toolCalls`、tool 结果是独立 `role:"tool"` + `toolCallId`），
 * 这样从历史进入也能复原工具 chip（调用中 / 已完成 / 待确认 / 出错），
 * 而不是过去那样把 tool part 全丢掉只剩纯文本。
 */
export interface ChatHistoryMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  /** assistant 消息上的工具调用（复原「调用中」chip + toolName 映射）。 */
  toolCalls?: { id: string; function: { name: string; arguments: string } }[];
  /** tool 结果消息回指的调用 id（复原「已完成 / 待确认 / 出错」chip）。 */
  toolCallId?: string;
}

async function mastraClient(): Promise<MastraClient> {
  const token = await getServiceToken();
  return new MastraClient({
    baseUrl: BACKENDS.mastra,
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** mastra(4111)单次读超时 —— 与 backendFetch 同档(5s)。 */
const MASTRA_READ_TIMEOUT_MS = 5000;

/**
 * 给 mastra 读包一层超时:`MastraClient` 自身不带显式超时,4111 卡住(重启/冷启/GC)时,
 * 调用方(activity 8s 轮询热路径 / 历史下拉)会一直挂死。超时即 reject,调用方 try/catch
 * 降级标 source 不可用,不拖垮整页。
 */
function withMastraTimeout<T>(p: Promise<T>, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<never>((_, reject) =>
      setTimeout(
        () => reject(new Error(`mastra ${label} 超时(${MASTRA_READ_TIMEOUT_MS}ms)`)),
        MASTRA_READ_TIMEOUT_MS,
      ),
    ),
  ]);
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
  const res = (await withMastraTimeout(
    client.listMemoryThreads({
      resourceId: CONSOLE_SUBJECT,
      agentId: AGENT_ID,
      perPage: limit,
      orderBy: { field: "updatedAt", direction: "DESC" },
    }),
    "listMemoryThreads",
  )) as { threads?: RawThread[] };

  const summaries: ChatThreadSummary[] = (res.threads ?? []).map((t) => ({
    id: t.id,
    // 清洗标题里夹带的 <page_context> 块（对话栏给消息加的页面上下文,不该进标题）。
    // 老会话标题被回填进了 envelope → 清洗后变 null → 进下面的回填重导出干净标题并持久化(自愈)。
    title: cleanTitle(t.title),
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
        // 从首条用户消息导出标题 —— 先剥掉 <page_context> 块,只留用户原话。
        const clean = cleanTitle(first.content);
        if (!clean) return;
        s.title = clean;
        // 持久化回写,下次轮询直接读 title、不再拉消息(自愈,避免每 8s 重复 fan-out)。
        void setChatThreadTitle(s.id, s.title).catch(() => {});
      } finally {
        backfillInflight.delete(s.id);
      }
    }),
  );

  return summaries;
}

/**
 * 取某会话的历史消息,展开成可渲染的 AG 消息序列。
 *
 * Mastra V2(AI SDK v4)把工具调用 + 结果存为 assistant 消息 `content.parts` 里的
 * `tool-invocation` part（一条 part 同时带 args 与 result）。这里把每条 DB 消息
 * 展开成前端 live 同构的序列：assistant（文本 + toolCalls）→ 若干 `role:"tool"`
 * 结果消息。过去只取 text part → 历史回填后工具 chip 全没了（本次修复点）。
 */
export async function listChatMessages(
  threadId: string,
): Promise<ChatHistoryMessage[]> {
  const client = await mastraClient();
  const res = (await withMastraTimeout(
    client.listThreadMessages(threadId, { agentId: AGENT_ID }),
    "listThreadMessages",
  )) as { messages?: RawMessage[] };
  return (res.messages ?? []).flatMap(expandDbMessage);
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

/**
 * 清洗会话标题:剥掉 <page_context> 块 + 截断到 60 字。空/纯 envelope → null。
 * 用于读取(老标题里混入了 envelope)与回填(从首条消息导出)两处,保证标题只显用户原话。
 */
function cleanTitle(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const cleaned = stripPageContext(raw).trim().slice(0, 60).trim();
  return cleaned || null;
}

/** 从 MastraDBMessage.content 取出 parts 数组（string / {parts} / 裸数组 三种形态容差）。 */
function partsOf(content: unknown): unknown[] {
  if (Array.isArray(content)) return content;
  if (content && typeof content === "object" && "parts" in content) {
    const p = (content as { parts?: unknown }).parts;
    if (Array.isArray(p)) return p;
  }
  return [];
}

/** MastraDBMessage.content 可能是 string,或 {parts:[{type,text}]},或多模态数组。 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  return partsOf(content)
    .map((p) =>
      p && typeof p === "object" && "type" in p && p.type === "text"
        ? String((p as { text?: unknown }).text ?? "")
        : "",
    )
    .join("");
}

interface ToolPart {
  toolCallId: string;
  toolName: string;
  args: unknown;
  result: unknown;
  hasResult: boolean;
}

/**
 * 把单个 part 识别为工具调用,兼容两种存储形态（Mastra 跨版本容差）：
 *  - AI SDK v4（当前 Mastra V2 schema）：`{ type:"tool-invocation", toolInvocation:{ state,
 *    toolCallId, toolName, args, result } }`
 *  - AI SDK v5 风格：`{ type:"dynamic-tool" | "tool-<name>", toolCallId, input, output, state }`
 * 不是工具 part → null。
 */
function toolPartOf(part: Record<string, unknown>): ToolPart | null {
  const type = typeof part.type === "string" ? part.type : "";

  // v4：tool-invocation（args/result 包在 toolInvocation 里）
  if (type === "tool-invocation") {
    const ti = part.toolInvocation;
    if (!ti || typeof ti !== "object") return null;
    const inv = ti as Record<string, unknown>;
    const toolCallId = typeof inv.toolCallId === "string" ? inv.toolCallId : undefined;
    if (!toolCallId) return null;
    return {
      toolCallId,
      toolName: typeof inv.toolName === "string" ? inv.toolName : "tool",
      args: inv.args,
      result: inv.result,
      hasResult: inv.state === "result" || "result" in inv,
    };
  }

  // v5：dynamic-tool / tool-<name>（input/output 直接在 part 上）
  if (type === "dynamic-tool" || (type.startsWith("tool-") && type !== "tool-invocation")) {
    const toolCallId = typeof part.toolCallId === "string" ? part.toolCallId : undefined;
    if (!toolCallId) return null;
    const toolName =
      typeof part.toolName === "string"
        ? part.toolName
        : type.startsWith("tool-")
          ? type.slice("tool-".length)
          : "tool";
    const hasOutput =
      "output" in part || (typeof part.state === "string" && part.state.includes("output"));
    return {
      toolCallId,
      toolName,
      args: part.input,
      result: part.output,
      hasResult: hasOutput,
    };
  }

  return null;
}

/** 工具 args / result 序列化为可展开展示的字符串（已是 string 则原样）。 */
function stringifyToolPayload(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === undefined || v === null) return "";
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/**
 * 把一条 DB 消息展开成 0..N 条 AG 消息，**严格保留 parts 原始顺序**。
 *
 * Mastra V2 把一轮 assistant 的「工具调用 + 结果」与「最终文字答复」存在同一条消息的
 * content.parts 里，真实顺序是 tool-call → tool-result → … → 最终文字。若把文字全拼
 * 成一条先发、工具结果再尾随，历史里工具 chip 会全堆到答复下方（= 会话最末，错位）。
 * 这里按 part 顺序走：
 *  - 连续文本累积成一个气泡，遇到工具 / 收尾即 flush（落在它本来的位置）
 *  - 每个工具 part → assistant(toolCalls) 占位（有结果时前端隐藏「调用中」chip）
 *    + 有 result 再补一条 role:"tool" 结果消息（渲染「已完成 / 待确认 / 出错」chip）
 */
function expandDbMessage(m: RawMessage): ChatHistoryMessage[] {
  if (m.role !== "user" && m.role !== "assistant") return [];
  const role = m.role;
  const baseId = m.id ?? crypto.randomUUID();
  const out: ChatHistoryMessage[] = [];
  const parts = partsOf(m.content);

  // 无 parts（content 是纯字符串）→ 单条文本消息。
  if (parts.length === 0) {
    const text = extractText(m.content);
    if (text.trim()) out.push({ id: baseId, role, content: text });
    return out;
  }

  let textBuf = "";
  let seg = 0;
  const flushText = () => {
    if (textBuf.trim()) {
      out.push({ id: `${baseId}:t${seg}`, role, content: textBuf });
      seg += 1;
    }
    textBuf = "";
  };

  for (const p of parts) {
    if (!p || typeof p !== "object") continue;
    const part = p as Record<string, unknown>;

    if (part.type === "text") {
      textBuf += String((part as { text?: unknown }).text ?? "");
      continue;
    }

    // 工具 part 只在 assistant 上有意义；其它 part（step-start / reasoning 等）跳过。
    const tp = role === "assistant" ? toolPartOf(part) : null;
    if (!tp) continue;

    flushText(); // 工具前的文字先落位
    out.push({
      id: `${baseId}:call:${tp.toolCallId}`,
      role: "assistant",
      content: "",
      toolCalls: [
        {
          id: tp.toolCallId,
          function: { name: tp.toolName, arguments: stringifyToolPayload(tp.args) },
        },
      ],
    });
    if (tp.hasResult) {
      out.push({
        id: `${baseId}:tool:${tp.toolCallId}`,
        role: "tool",
        content: stringifyToolPayload(tp.result),
        toolCallId: tp.toolCallId,
      });
    }
  }
  flushText(); // 收尾文字（通常是最终答复）

  return out;
}

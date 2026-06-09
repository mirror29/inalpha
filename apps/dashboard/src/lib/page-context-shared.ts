/**
 * 页面上下文 —— **纯函数 + 类型**（无 React / 无 "use client"），供 client 组件与
 * server 代码（如 `lib/mastra.ts` 拉历史会话时清洗标题）共用。
 *
 * client-only 的 `usePageContext()` 在 `page-context.ts`（"use client"）里，并 re-export
 * 这里的全部成员，老的 `@/lib/page-context` 导入无需改动。
 */

/** 控制台页面类型 —— 与 orchestrator INSTRUCTIONS 的 page_context 小节一一对应。 */
export type PageKind =
  | "runner_detail"
  | "candidate_detail"
  | "runners_list"
  | "lab_list"
  | "factors"
  | "risk"
  | "activity"
  | "divination"
  | "overview";

export interface PageContext {
  /** 页面类型(机器可读,英文,与 UI locale 解耦)。 */
  kind: PageKind;
  /** 详情页的实体 id(runner_detail=run_id / candidate_detail=candidate_id);列表/总览无。 */
  id?: string;
  /** 不含 locale 前缀的路径,如 `/runners/<uuid>`。 */
  pathname: string;
}

/** UUID 判定 —— 与 `api/runners/[id]/route.ts` 同款,确认路径段是实体 id 而非子页面。 */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * 解析路径为页面上下文。纯函数,便于测试。
 * @param pathname `usePathname()` 返回的 locale-less 路径
 */
export function parsePageContext(pathname: string): PageContext {
  // 去掉首尾斜杠后按段切分;空串 = 根路径(总览)。
  const segs = pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const [head, second] = segs;

  switch (head) {
    case undefined:
      return { kind: "overview", pathname };
    case "runners":
      return second && UUID_RE.test(second)
        ? { kind: "runner_detail", id: second, pathname }
        : { kind: "runners_list", pathname };
    case "lab":
      return second && UUID_RE.test(second)
        ? { kind: "candidate_detail", id: second, pathname }
        : { kind: "lab_list", pathname };
    case "factors":
      return { kind: "factors", pathname };
    case "risk":
      return { kind: "risk", pathname };
    case "activity":
      return { kind: "activity", pathname };
    case "divination":
      return { kind: "divination", pathname };
    default:
      // 未知路由 → 当总览兜底,避免误带具体实体上下文。
      return { kind: "overview", pathname };
  }
}

/**
 * 给 agent 的机器可读上下文块 —— 拼在用户消息 content 开头。
 * 键名一律英文,保证 agent 稳定解析(回复语言仍随用户)。详情页带 id,列表/总览只给 page。
 */
export function buildPageContextEnvelope(ctx: PageContext): string {
  const lines = [`page=${ctx.kind}`];
  if (ctx.kind === "runner_detail" && ctx.id) lines.push(`run_id=${ctx.id}`);
  if (ctx.kind === "candidate_detail" && ctx.id)
    lines.push(`candidate_id=${ctx.id}`);
  lines.push(`path=${ctx.pathname}`);
  return `<page_context>\n${lines.join("\n")}\n</page_context>\n\n`;
}

/** 匹配消息开头的完整 page_context 块(含尾随空行),用于渲染时还原用户原话。 */
export const PAGE_CONTEXT_RE = /^<page_context>[\s\S]*?<\/page_context>\n*/;

/**
 * 剥掉开头的 page_context 块 —— 用户气泡只显示原话,历史回填的旧消息同样干净。
 *
 * 兜底:若闭合标签被截断(如标题 slice(0,60) 截在中间),仍把残缺的开头块整段去掉,
 * 避免标题/正文里残留 `<page_context> page=...`。
 */
export function stripPageContext(text: string): string {
  const stripped = text.replace(PAGE_CONTEXT_RE, "");
  if (stripped.includes("<page_context>")) {
    return stripped.replace(/<page_context>[\s\S]*$/, "").trimStart();
  }
  return stripped;
}

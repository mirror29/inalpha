"use client";

import { usePathname } from "@/i18n/navigation";

import { parsePageContext, type PageContext } from "./page-context-shared";

/**
 * 页面上下文感知（dashboard 面板 × 对话栏融合）。
 *
 * 对话栏挂在 `[locale]/layout.tsx`,路由切换不重挂载;`usePathname()`(来自
 * `@/i18n/navigation`,**不含 locale 前缀**)随导航更新 → 把「用户此刻在看哪个页面」
 * 解析成结构化 {kind, id},随消息以 `<page_context>` 块拼进 content 传给 agent。
 *
 * 纯函数 + 类型在 `page-context-shared.ts`（无 "use client"），server 侧（`lib/mastra.ts`
 * 清洗历史会话标题）也复用 `stripPageContext`;这里仅补 client-only 的路由 hook，并把
 * 共享成员 re-export 出去,`@/lib/page-context` 的老导入无需改动。
 */

export {
  PAGE_CONTEXT_RE,
  buildPageContextEnvelope,
  parsePageContext,
  stripPageContext,
} from "./page-context-shared";
export type { PageContext, PageKind } from "./page-context-shared";

/** locale 感知的当前页面上下文。 */
export function usePageContext(): PageContext {
  return parsePageContext(usePathname());
}

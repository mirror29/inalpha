/**
 * Web 搜索 tool 包装 —— services/data 的 web 搜索端点。
 *
 * D-10 新增：后端用 ddgs 聚合多引擎（Bing/DuckDuckGo/Google/Brave），
 * 零 API key，自动按中文/英文选最优引擎。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import { getSettings } from "../config.js";

type ToolRequestContext = { authToken?: string };

async function getBaseUrl(): Promise<string> {
  const settings = getSettings();
  return settings.dataServiceUrl;
}

async function getAuthHeaders(ctx?: ToolRequestContext): Promise<Record<string, string>> {
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return { Authorization: `Bearer ${token}` };
}

// ────────────────────────────────────────────────────────────────────
// web.search
// ────────────────────────────────────────────────────────────────────

export const webSearchTool = createTool({
  id: "web.search",
  description: `
    搜索互联网获取实时信息。后端用 ddgs 聚合多引擎（Bing/DuckDuckGo/Google/Brave），
    零 API key，自动按中文/英文选最优引擎。

    何时用：
    - 研究前补充最新信息："茅台 2026 Q1 财报" / "BTC ETF 最新消息"
    - 用户问"最近有什么新闻"但你现有的数据源没有
    - 验证 LLM 训练记忆里的信息是否过时
    - deep_dive 之前预搜索，把搜索结果喂给 analysts

    何时不用：
    - 已有 akshare/yfinance 专用数据源覆盖的场景（优先用专用数据源）
    - 实时价格 → data.get_ticker
    - 历史 K 线 → data.get_bars

    坑：
    - ddgs 偶发限速，失败时返回空结果（静默降级）
    - 搜索结果质量因 engine 而异；中文自动走 bing 后端
    - 不要在循环里高频调用
  `.trim(),
  inputSchema: z.object({
    query: z.string().min(1).max(500).describe("搜索关键词"),
    backend: z.enum(["auto", "bing", "duckduckgo", "google", "brave"]).default("auto")
      .describe("搜索后端；auto 自动检测中文→bing"),
    maxResults: z.number().int().min(1).max(20).default(10),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const baseUrl = await getBaseUrl();
    const headers = await getAuthHeaders(tc);
    const url = new URL(`${baseUrl}/web/search`);
    url.searchParams.set("query", inputData.query);
    url.searchParams.set("backend", inputData.backend ?? "auto");
    url.searchParams.set("max_results", String(inputData.maxResults ?? 10));
    try {
      const r = await fetch(url.toString(), { headers });
      if (!r.ok) return { results: [], error: `HTTP ${r.status}` };
      return await r.json();
    } catch (err) {
      return { results: [], error: String(err) };
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// web.search_news
// ────────────────────────────────────────────────────────────────────

export const webSearchNewsTool = createTool({
  id: "web.search_news",
  description: `
    搜索新闻。后端用 ddgs 的 news 模式，返回最近新闻头条。

    何时用：用户问"最近有什么新闻" / 研究前了解最新动态
    何时不用：已有专用数据源（如 sentiment analyst 会自动调用）

    坑：ddgs 新闻覆盖不如专业新闻 API 广；中文财经新闻有限
  `.trim(),
  inputSchema: z.object({
    query: z.string().min(1).max(500).describe("新闻搜索关键词"),
    maxResults: z.number().int().min(1).max(20).default(10),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const baseUrl = await getBaseUrl();
    const headers = await getAuthHeaders(tc);
    const url = new URL(`${baseUrl}/web/news`);
    url.searchParams.set("query", inputData.query);
    url.searchParams.set("max_results", String(inputData.maxResults ?? 10));
    try {
      const r = await fetch(url.toString(), { headers });
      if (!r.ok) return { results: [], error: `HTTP ${r.status}` };
      return await r.json();
    } catch (err) {
      return { results: [], error: String(err) };
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// web.fetch
// ────────────────────────────────────────────────────────────────────

export const webFetchTool = createTool({
  id: "web.fetch",
  description: `
    抓取一个 URL 的网页正文（trafilatura 抽正文 + 标题 + 发布日期）。
    证据链最后一公里：web.search 只有标题 + snippet，结论级证据要用本 tool
    读原文（财报 / 公告 / 新闻稿 / transcript 页面）。

    何时用：
    - web.search / web.search_news 找到关键来源后，引用其内容下结论前先读原文
    - 给证据定强度等级（强 / 中 / 弱）需要确认页面实际说了什么
    - 需要页面发布日期来标注数据截止

    何时不用：
    - 行情 / K 线 / 财务指标 → data.*（结构化数据源更准）
    - 还没确定哪个 URL 值得读 → 先 web.search 筛选，别逐个乱抓

    坑：
    - 内网 / 私有地址被拒（SSRF 护栏）；非 HTML 文本类型被拒（PDF 读不了）
    - 正文超长会截断（truncated=true）；published_at 抽不到时为 null，
      此时不要编日期，按"日期待核"处理
    - 部分站点反爬返 403/超时——错误时换一个来源，不要对同一 URL 反复重试
  `.trim(),
  inputSchema: z.object({
    url: z.string().url().max(2048).describe("要抓取的 http/https URL"),
    maxChars: z.number().int().min(100).max(200_000).optional()
      .describe("正文字符上限（默认 4 万，受服务端钳制）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const baseUrl = await getBaseUrl();
    const headers = await getAuthHeaders(tc);
    const url = new URL(`${baseUrl}/web/fetch`);
    url.searchParams.set("url", inputData.url);
    if (inputData.maxChars) url.searchParams.set("max_chars", String(inputData.maxChars));
    try {
      const r = await fetch(url.toString(), { headers });
      if (!r.ok) return { url: inputData.url, text: "", error: `HTTP ${r.status}` };
      return await r.json();
    } catch (err) {
      return { url: inputData.url, text: "", error: String(err) };
    }
  },
});

export const webTools = [webSearchTool, webSearchNewsTool, webFetchTool] as const;

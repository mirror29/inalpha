import { NextRequest, NextResponse } from "next/server";

import { BackendError, backendFetch, CONSOLE_SUBJECT } from "@/lib/backend";

export const dynamic = "force-dynamic";

/**
 * GET /api/divination/history?limit= —— 占卜台历史记录(BFF)。
 *
 * 转发到 mastra 的 `GET /divination/history`,注入 `subject = CONSOLE_SUBJECT`
 * (只看当前控制台身份的记录)。limit 透传(mastra 侧封顶 100)。
 */
export async function GET(req: NextRequest) {
  const limit = req.nextUrl.searchParams.get("limit") ?? undefined;
  try {
    const data = await backendFetch<{ records: unknown[] }>("mastra", "/divination/history", {
      timeoutMs: 8000,
      query: { subject: CONSOLE_SUBJECT, limit },
    });
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof BackendError) {
      return NextResponse.json({ error: err.message, detail: err.detail }, { status: err.status });
    }
    return NextResponse.json({ error: "divination history failed" }, { status: 500 });
  }
}

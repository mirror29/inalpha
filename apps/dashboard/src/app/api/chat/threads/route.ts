import { NextResponse } from "next/server";

import { listChatThreads } from "@/lib/mastra";

export const dynamic = "force-dynamic";

/**
 * GET /api/chat/threads —— 历史会话列表(当前 resource 的 mastra memory threads)。
 * 取不到(mastra:4111 没起)时返回空列表 + sourceDown,不抛错拖垮对话栏。
 */
export async function GET() {
  try {
    const threads = await listChatThreads();
    return NextResponse.json(
      { threads, sourceDown: false },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { threads: [], sourceDown: true },
      { headers: { "Cache-Control": "no-store" } },
    );
  }
}

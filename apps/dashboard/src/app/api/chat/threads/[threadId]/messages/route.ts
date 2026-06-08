import { NextResponse } from "next/server";

import { listChatMessages } from "@/lib/mastra";

export const dynamic = "force-dynamic";

/**
 * GET /api/chat/threads/:threadId/messages —— 某历史会话的消息(简化纯文本)。
 * 切换会话时前端用它回填对话 UI。取不到时返回空列表。
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await params;
  try {
    const messages = await listChatMessages(threadId);
    return NextResponse.json(
      { messages },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { messages: [] },
      { headers: { "Cache-Control": "no-store" } },
    );
  }
}

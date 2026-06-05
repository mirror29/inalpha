import { NextResponse } from "next/server";

import { setChatThreadTitle } from "@/lib/mastra";

export const dynamic = "force-dynamic";

/**
 * POST /api/chat/threads/:threadId/title —— 设置会话标题。
 * 前端在「发起会话首条消息」时调用,用消息内容当标题,方便日志/历史回溯。
 * 失败静默(标题是锦上添花,不能拖垮发送)。
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await params;
  try {
    const body = (await req.json()) as { title?: string };
    if (body.title) await setChatThreadTitle(threadId, body.title);
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false });
  }
}

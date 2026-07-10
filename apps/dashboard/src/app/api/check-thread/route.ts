import { NextRequest, NextResponse } from "next/server";

import { checkThreadOwnership } from "@/lib/mastra";

/**
 * 校验 threadId 是否属于当前登录用户。
 *
 * POST body: { threadId: string }
 * 返回: { valid: boolean }
 *
 * 前端 mount 时调用——localStorage 里的 threadId 若不属于当前用户（切换用户后），
 * 则生成新 threadId，避免发起会话时 403 "thread belongs to a different resource"。
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const threadId = body.threadId;
    if (!threadId || typeof threadId !== "string") {
      return NextResponse.json({ valid: false }, { status: 400 });
    }
    const valid = await checkThreadOwnership(threadId);
    return NextResponse.json({ valid });
  } catch {
    return NextResponse.json({ valid: false }, { status: 500 });
  }
}
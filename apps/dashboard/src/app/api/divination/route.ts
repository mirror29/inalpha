import { NextRequest, NextResponse } from "next/server";

import { BackendError, backendFetch, getSessionSubject } from "@/lib/backend";

export const dynamic = "force-dynamic";

/**
 * POST /api/divination —— 占卜台直算端点(BFF)。
 *
 * 转发到 mastra 的 `POST /divination/cast`(纯计算、**无 LLM**、确定性),注入控制台
 * 身份 `subject`(经 getSessionSubject() 从登录用户派生,dev 回落 console:dev)做历史隶属。结果由 mastra 落库,这里只透传返回。
 *
 * 设计:狐神签独立模块点按钮 → 本路由 → mastra 直算 → 瞬时出卦,**不触发对话栏会话**;
 * 会话式深度解读由用户主动在对话栏触发(走 `/api/copilotkit`)。
 */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }

  try {
    const record = await backendFetch<unknown>("mastra", "/divination/cast", {
      method: "POST",
      timeoutMs: 8000,
      body: {
        mode: body["mode"],
        question: body["question"],
        symbol: body["symbol"],
        subject: await getSessionSubject(),
      },
    });
    return NextResponse.json(record);
  } catch (err) {
    if (err instanceof BackendError) {
      return NextResponse.json({ error: err.message, detail: err.detail }, { status: err.status });
    }
    return NextResponse.json({ error: "divination cast failed" }, { status: 500 });
  }
}

import { NextResponse } from "next/server";

import { readSession } from "@/lib/session";

/**
 * 当前登录用户(供侧栏显示 email + 登出按钮判存在)。未登录 / 未启用登录 → `{ user: null }`。
 * 不返回任何凭据。
 */
export async function GET(): Promise<Response> {
  const session = await readSession();
  return NextResponse.json({
    user: session ? { email: session.email, subject: session.subject } : null,
  });
}

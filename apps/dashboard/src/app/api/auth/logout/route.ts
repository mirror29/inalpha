import { NextResponse } from "next/server";

import { SESSION_COOKIE, SESSION_COOKIE_OPTS } from "@/lib/session";

/** 登出:清 session cookie。前端随后跳 /login。 */
export async function POST(): Promise<Response> {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "", { ...SESSION_COOKIE_OPTS, maxAge: 0 });
  return res;
}

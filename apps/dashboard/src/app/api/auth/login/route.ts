import { NextResponse } from "next/server";

import { BackendError, backendFetch } from "@/lib/backend";
import { SESSION_COOKIE, SESSION_COOKIE_OPTS, createSessionToken } from "@/lib/session";

/**
 * 登录:校验凭据 → 落 session cookie。
 *
 * dashboard 无 DB 凭据,把邮箱 / 密码反代到内网 paper `/auth/login` 校验;成功后用
 * `JWT_SECRET` 签 httpOnly session cookie。密码只透传一次,不落任何日志。
 */
export async function POST(req: Request): Promise<Response> {
  let email: unknown;
  let password: unknown;
  try {
    ({ email, password } = await req.json());
  } catch {
    return NextResponse.json({ error: "请求体格式错误" }, { status: 400 });
  }
  if (typeof email !== "string" || typeof password !== "string" || !email || !password) {
    return NextResponse.json({ error: "缺少邮箱或密码" }, { status: 400 });
  }

  try {
    const user = await backendFetch<{ subject: string; email: string; roles: string[] }>(
      "paper",
      "/auth/login",
      { auth: false, method: "POST", body: { email, password } },
    );
    const token = await createSessionToken({
      subject: user.subject,
      email: user.email,
      roles: user.roles ?? [],
    });
    const res = NextResponse.json({ ok: true });
    res.cookies.set(SESSION_COOKIE, token, {
      ...SESSION_COOKIE_OPTS,
      maxAge: 7 * 24 * 3600,
    });
    return res;
  } catch (err) {
    if (err instanceof BackendError && err.status === 401) {
      return NextResponse.json({ error: "邮箱或密码不正确" }, { status: 401 });
    }
    return NextResponse.json({ error: "登录服务暂不可用,请稍后重试" }, { status: 502 });
  }
}

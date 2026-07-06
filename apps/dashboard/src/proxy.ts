import createMiddleware from "next-intl/middleware";
import { type NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";
import { routing } from "@/i18n/routing";

/**
 * 中间件 = 登录闸门 + next-intl locale 协商。
 *
 * `AUTH_ENABLED=true`(线上):无有效 session 时 —— `/api/*` 返 401,页面重定向 `/login`。
 * `AUTH_ENABLED=false`(本地 dev 默认):完全等价于只跑 next-intl(加登录前的行为)。
 *
 * 注意:middleware 跑在 edge runtime,不能 import server-only 的 `session.ts`,故这里
 * 重复 cookie 名 / secret 读取。校验用 jose(edge 友好)。
 */

const intl = createMiddleware(routing);

// 生产强制开(fail-safe):NODE_ENV=production 恒为 true,配置缺失/拼错也不静默放行。
// 仅非生产(本地 dev)靠 AUTH_ENABLED=true 显式 opt-in。与 session.ts 同一判断。
const AUTH_ENABLED =
  process.env.AUTH_ENABLED === "true" || process.env.NODE_ENV === "production";
const SESSION_COOKIE = "inalpha_session";

function getSecret(): Uint8Array | null {
  const secret = process.env.JWT_SECRET;
  return secret ? new TextEncoder().encode(secret) : null;
}

async function hasValidSession(req: NextRequest): Promise<boolean> {
  const raw = req.cookies.get(SESSION_COOKIE)?.value;
  const secret = getSecret();
  if (!raw || !secret) return false;
  try {
    await jwtVerify(raw, secret, { algorithms: ["HS256"] });
    return true;
  } catch {
    return false;
  }
}

export default async function middleware(req: NextRequest): Promise<Response> {
  const { pathname } = req.nextUrl;
  const isApi = pathname.startsWith("/api");
  // 公开:登录页 + 登录/登出/会话 API(否则登录前无从进入)。
  const isPublic = pathname === "/login" || pathname.startsWith("/api/auth/");

  if (AUTH_ENABLED && !isPublic && !(await hasValidSession(req))) {
    if (isApi) {
      return NextResponse.json({ error: "UNAUTHORIZED" }, { status: 401 });
    }
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    // 记住原始目标,登录后跳回(LoginForm 只接受站内相对路径)。
    url.searchParams.set("from", pathname);
    return NextResponse.redirect(url);
  }

  // /api 与 /login 不走 locale 协商;其余页面交给 next-intl。
  if (isApi || pathname === "/login") {
    return NextResponse.next();
  }
  return intl(req);
}

export const config = {
  // 覆盖页面 + /api(为拦 API);排除 Next 内部资源与静态文件。
  matcher: ["/((?!_next|_vercel|.*\\..*).*)"],
};

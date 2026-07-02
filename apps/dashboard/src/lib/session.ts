import "server-only";

import { cookies } from "next/headers";
import { SignJWT, jwtVerify } from "jose";

/**
 * 登录会话层。
 *
 * 与 `backend.ts` 的分工:
 *  - 本模块 = **登录态**。用 `JWT_SECRET` 签一个 httpOnly session cookie(长效 7d),
 *    存登录用户的 subject / email / roles。
 *  - `backend.ts` = **后端调用**。每请求读本模块的 session 拿 subject,再铸一个短效(1h)
 *    后端 token 打给 Python / mastra。
 *
 * `AUTH_ENABLED=false`(本地 dev 默认)时整套登录不启用:middleware 放行、`backend.ts`
 * 回落固定 `CONSOLE_SUBJECT`,开发体验与加登录前完全一致。线上 `AUTH_ENABLED=true` 才强制登录。
 */

/** session cookie 名。middleware(`proxy.ts`)也用这个字面量(edge 不能 import 本 server-only 模块)。 */
export const SESSION_COOKIE = "inalpha_session";

/** 线上开、dev 关。middleware 里各自读 env,不共享本常量(避免 edge 打包 server-only)。 */
export const AUTH_ENABLED = process.env.AUTH_ENABLED === "true";

const ALG = "HS256";
/** session 有效期:7 天。过期即重登(单用户,无刷新令牌)。 */
export const SESSION_TTL_SEC = 7 * 24 * 3600;

/**
 * session token 的区分标记。session cookie 与后端 service token 同用 `JWT_SECRET` 签、
 * claim 形状一致——不加区分,泄露的 session cookie 可被当长效后端凭据重放。session token
 * 带 `token_use:"session"`,后端 `get_current_user` 一律拒收(service token 不带此 claim)。
 */
const SESSION_TOKEN_USE = "session";

function getSecret(): Uint8Array {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    throw new Error("JWT_SECRET 未配置:无法签发 / 校验 session。");
  }
  return new TextEncoder().encode(secret);
}

/** 登录用户身份(不含任何凭据)。 */
export interface SessionUser {
  subject: string;
  email: string;
  roles: string[];
}

/** 签一个 session JWT(HS256,7d)。登录成功后写进 httpOnly cookie。 */
export async function createSessionToken(user: SessionUser): Promise<string> {
  const nowSec = Math.floor(Date.now() / 1000);
  return new SignJWT({ email: user.email, roles: user.roles, token_use: SESSION_TOKEN_USE })
    .setProtectedHeader({ alg: ALG })
    .setSubject(user.subject)
    .setIssuedAt(nowSec)
    .setExpirationTime(nowSec + SESSION_TTL_SEC)
    .sign(getSecret());
}

/** 读并校验当前请求的 session cookie。无 / 无效 / 过期一律返回 null。 */
export async function readSession(): Promise<SessionUser | null> {
  const jar = await cookies();
  const raw = jar.get(SESSION_COOKIE)?.value;
  if (!raw) return null;
  try {
    const { payload } = await jwtVerify(raw, getSecret(), { algorithms: [ALG] });
    // 只认 session token(带 token_use=session)——service token 不能拿来当登录态。
    if (!payload.sub || payload.token_use !== SESSION_TOKEN_USE) return null;
    return {
      subject: payload.sub,
      email: typeof payload.email === "string" ? payload.email : "",
      roles: Array.isArray(payload.roles) ? (payload.roles as string[]) : [],
    };
  } catch {
    return null;
  }
}

/** 是否应把 session cookie 标记 Secure —— 生产(HTTPS 经 Cloudflare)开,本地 http 关。 */
export const SESSION_COOKIE_SECURE = process.env.NODE_ENV === "production";

/** session cookie 的统一写入选项(登录 set / 登出 clear 共用)。 */
export const SESSION_COOKIE_OPTS = {
  httpOnly: true,
  secure: SESSION_COOKIE_SECURE,
  sameSite: "lax" as const,
  path: "/",
};

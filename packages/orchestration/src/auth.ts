/**
 * JWT mint + verify 工具。
 *
 * 两种使用模式：
 *
 * - **用户对话**：从 Next.js 请求拿到用户 JWT，forward 给 service（D-8 起）
 * - **后台任务 / smoke test**：用 ``mintServiceToken()`` 自签一个服务 token
 *
 * 跟 services/_shared/auth.py 共用同一个 JWT_SECRET（env 配同一值）。
 */
import { SignJWT, jwtVerify } from "jose";

import { getSettings } from "./config.js";

type Payload = {
  sub: string;
  email?: string;
  roles?: string[];
  [key: string]: unknown;
};

/**
 * 自签一个 service token，给 smoke test / cron / 后台 worker 用。
 *
 * 默认 TTL 1 小时。生产场景用户 token 直接 forward，不需要走这个。
 */
export async function mintServiceToken(
  payload: Payload = { sub: "service:orchestration" },
  ttlSeconds = 3600,
): Promise<string> {
  const secret = new TextEncoder().encode(getSettings().jwtSecret);
  const now = Math.floor(Date.now() / 1000);
  return await new SignJWT({ ...payload })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(payload.sub)
    .setIssuedAt(now)
    .setExpirationTime(now + ttlSeconds)
    .sign(secret);
}

/**
 * Agent 后台调后端时的默认身份 ``sub``。
 *
 * 单租户 dev 下对齐控制台账户（``getSettings().consoleSubject``，默认 ``"console:dev"``），
 * 使 agent 写的 live run / 订单 / 候选与控制台读取落到同一个 ``account_id``。
 * 工具应在缺少调用者 token 时回落到这个 sub：
 * ``ctx?.authToken ?? mintServiceToken({ sub: defaultServiceSubject() })``。
 */
export function defaultServiceSubject(): string {
  return getSettings().consoleSubject;
}

/**
 * 验签 + 解码 JWT。失败抛 ``Error``（让 caller 决定怎么响应）。
 */
export async function verifyToken(token: string): Promise<Payload> {
  const secret = new TextEncoder().encode(getSettings().jwtSecret);
  const { payload } = await jwtVerify(token, secret, {
    algorithms: ["HS256"],
  });
  return payload as Payload;
}

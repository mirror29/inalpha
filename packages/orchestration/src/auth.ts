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
import { AUTH_SUB_KEY } from "./hooks/with-hooks.js";

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
 * 从 tool 的 ``ctx.requestContext`` 解析打给下游 service 的 token（多用户隔离关键）。
 *
 * 优先级：
 *  1. **显式 ``authToken``**（scheduler tool-mode 塞的 plain object）——直接 forward。
 *  2. **HTTP 中间件注入的已认证 ``sub``**（``RequestContext[AUTH_SUB_KEY]``，由 mastra
 *     ``identityMiddleware`` 从 Bearer 提取）——按登录用户 ``sub`` 铸 token，使 agent
 *     发起的写操作（start_strategy / execute_plan / 下单 …）落到该用户的 ``account_id``。
 *  3. 都没有 → service subject 兜底（后台任务 / dev 未登录）。
 *
 * ⚠️ 历史坑：工具过去只读 ``ctx?.authToken``，而 HTTP 路径下 RequestContext 是 Map 实例、
 * sub 存在 ``AUTH_SUB_KEY`` 下（不是 ``.authToken`` 属性），导致恒落兜底 —— 多用户下 agent
 * 写操作全落到 ``console:dev``。本函数同时兼容 ``.authToken`` 属性与 ``.get(AUTH_SUB_KEY)``。
 */
export async function resolveRequestToken(rc?: {
  authToken?: string;
  get?: (key: string) => unknown;
}): Promise<string> {
  if (typeof rc?.authToken === "string" && rc.authToken) {
    return rc.authToken;
  }
  const sub = typeof rc?.get === "function" ? rc.get(AUTH_SUB_KEY) : undefined;
  if (typeof sub === "string" && sub) {
    return await mintServiceToken({ sub });
  }
  return await mintServiceToken({ sub: defaultServiceSubject() });
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

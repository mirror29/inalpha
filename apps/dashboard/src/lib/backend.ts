import "server-only";

import { SignJWT } from "jose";

/**
 * BFF 后端接入层(**仅 server 侧**,`server-only` 防止误打包进浏览器)。
 *
 * 职责:
 *  1. 持有后端 base url(从 env 读,默认本地端口)。
 *  2. `getServiceToken()` —— 用根 .env 的 JWT_SECRET 自签一个 dev token。
 *     后端按 `sub` 派生稳定 account_id(services/paper/.../account_id.py),
 *     所以控制台始终落到同一个账户。
 *  3. `backendFetch()` —— 带 Authorization 的 fetch,统一超时 + 错误。
 *
 * 这是「鉴权」唯一的一层:产品化(接真实登录/多租户)时只换 getServiceToken()
 * 的 subject 来源,页面与数据层不返工(见设计文档 §鉴权)。
 */

export const BACKENDS = {
  paper: process.env.PAPER_SERVICE_URL ?? "http://127.0.0.1:8002",
  data: process.env.DATA_SERVICE_URL ?? "http://127.0.0.1:8001",
  research: process.env.RESEARCH_SERVICE_URL ?? "http://127.0.0.1:8003",
  mastra: process.env.MASTRA_URL ?? "http://127.0.0.1:4111",
  factor: process.env.FACTOR_SERVICE_URL ?? "http://127.0.0.1:8004",
} as const;

export type BackendName = keyof typeof BACKENDS;

const SUBJECT = process.env.CONSOLE_SUBJECT ?? "console:dev";
const EMAIL = process.env.CONSOLE_EMAIL ?? "console@inalpha.dev";
const ALG = process.env.JWT_ALGORITHM ?? "HS256";

/** 进程内缓存,避免每个请求都重签;到期前 60s 续签。 */
let cached: { token: string; exp: number } | null = null;

function getSecret(): Uint8Array {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    throw new BackendError(
      500,
      "JWT_SECRET 未配置:在 apps/dashboard/.env.local 填入与后端一致的 JWT_SECRET。",
    );
  }
  return new TextEncoder().encode(secret);
}

/**
 * 自签 dev token(HS256 等对称算法 —— 后端只接受 HS256/384/512)。
 * payload 形状对齐各 service tests/conftest.py 的 make_test_token:{sub,email,exp}。
 */
export async function getServiceToken(): Promise<string> {
  const nowSec = Math.floor(Date.now() / 1000);
  if (cached && cached.exp - 60 > nowSec) {
    return cached.token;
  }
  const ttl = 3600;
  const exp = nowSec + ttl;
  const token = await new SignJWT({ email: EMAIL })
    .setProtectedHeader({ alg: ALG })
    .setSubject(SUBJECT)
    .setIssuedAt(nowSec)
    .setExpirationTime(exp)
    .sign(getSecret());
  cached = { token, exp };
  return token;
}

/** 后端错误 —— 带 HTTP 状态,便于 Route Handler 决定回给前端的 code。 */
export class BackendError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "BackendError";
  }
}

interface FetchOptions {
  /** query 参数(undefined 值自动跳过)。 */
  query?: Record<string, string | number | boolean | undefined>;
  /** 超时(ms),默认 10s。 */
  timeoutMs?: number;
  /** 是否要鉴权,默认 true。 */
  auth?: boolean;
  /** HTTP 方法,默认 GET。 */
  method?: "GET" | "POST";
  /** POST body(对象会 JSON 序列化)。 */
  body?: unknown;
}

/**
 * 调一个后端 service 的 GET 接口,带 dev token,统一超时 / 错误。
 * 失败抛 BackendError —— 调用方(Route Handler / 聚合)决定是整页失败还是降级。
 */
export async function backendFetch<T>(
  backend: BackendName,
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const { query, timeoutMs = 10_000, auth = true, method = "GET", body } = opts;
  const url = new URL(path, BACKENDS[backend]);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (auth) headers.Authorization = `Bearer ${await getServiceToken()}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    throw new BackendError(
      aborted ? 504 : 502,
      aborted
        ? `${backend} 请求超时(${timeoutMs}ms)`
        : `无法连接 ${backend}(${BACKENDS[backend]})`,
      err instanceof Error ? err.message : String(err),
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text().catch(() => undefined);
    }
    throw new BackendError(res.status, `${backend} 返回 ${res.status}`, detail);
  }

  return (await res.json()) as T;
}

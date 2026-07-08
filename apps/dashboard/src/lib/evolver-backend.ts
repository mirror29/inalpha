/**
 * evolver 服务后端 fetch 封装。
 * 由于 evolver 服务可能使用不同的 auth 方案（或当前无 auth），
 * 这里用独立的后端 fetch，走 evolver 的 base URL。
 */
import { BackendError, BACKENDS, getServiceToken } from "./backend";

interface FetchOptions {
  query?: Record<string, string | number | boolean | undefined>;
  timeoutMs?: number;
  auth?: boolean;
  method?: "GET" | "POST";
  body?: unknown;
}

export async function evolutionBackendFetch<T>(
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const { query, timeoutMs = 10_000, auth = false, method = "GET", body } = opts;
  const baseUrl = BACKENDS.evolver;
  const url = new URL(path, baseUrl);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (auth) {
    headers.Authorization = `Bearer ${await getServiceToken()}`;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    throw new BackendError(
      0,
      err instanceof Error ? err.message : `EVOLVER_UNREACHABLE`,
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let detail: unknown;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      // ignore
    }
    throw new BackendError(res.status, `evolver ${res.status}`, detail);
  }

  return (await res.json()) as T;
}
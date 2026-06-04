/** 客户端 SWR fetcher —— 同源 /api/* GET,JSON,带结构化错误。 */

export class FetchError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "FetchError";
  }
}

export async function jsonFetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let body: { error?: string; detail?: unknown } = {};
    try {
      body = await res.json();
    } catch {
      /* 非 JSON 错误体,忽略 */
    }
    throw new FetchError(
      res.status,
      body.error ?? `HTTP ${res.status}`,
      body.detail,
    );
  }
  return (await res.json()) as T;
}

/**
 * 通用 HTTP client wrapper —— 给 data + paper clients 共享。
 *
 * 特性：
 *
 * - 自动注入 ``Authorization: Bearer <jwt>``
 * - 30s 默认超时（AbortController）
 * - 上游 ``{ code, message, details }`` 错误原样保留（不重新包装）
 * - 调用方负责 close / 单次调用即关
 */

export class HttpClientError extends Error {
  public readonly code: string;
  public readonly status: number;
  public readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      code: string;
      status: number;
      details?: Record<string, unknown>;
      cause?: unknown;
    },
  ) {
    super(message, { cause: options.cause });
    this.name = "HttpClientError";
    this.code = options.code;
    this.status = options.status;
    this.details = options.details ?? {};
  }
}

export type HttpClientOptions = {
  baseUrl: string;
  token: string;
  timeoutMs?: number;
};

export class HttpClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly timeoutMs: number;

  constructor(options: HttpClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.token = options.token;
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  async get<T>(
    path: string,
    query?: Record<string, string | number | boolean | undefined>,
  ): Promise<T> {
    const url = this.buildUrl(path, query);
    return await this.request<T>("GET", url);
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    const url = this.buildUrl(path);
    return await this.request<T>("POST", url, body);
  }

  private buildUrl(
    path: string,
    query?: Record<string, string | number | boolean | undefined>,
  ): string {
    const url = new URL(this.baseUrl + (path.startsWith("/") ? path : `/${path}`));
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null) {
          url.searchParams.set(k, String(v));
        }
      }
    }
    return url.toString();
  }

  private async request<T>(method: string, url: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers: {
          "Authorization": `Bearer ${this.token}`,
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      if ((err as { name?: string }).name === "AbortError") {
        throw new HttpClientError(`request timed out after ${this.timeoutMs}ms: ${url}`, {
          code: "REQUEST_TIMEOUT",
          status: 504,
          cause: err,
        });
      }
      throw new HttpClientError(`failed to reach ${url}: ${String(err)}`, {
        code: "UPSTREAM_UNREACHABLE",
        status: 502,
        cause: err,
      });
    }
    clearTimeout(timer);

    const text = await response.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = { message: text };
      }
    }

    if (!response.ok) {
      const body = parsed as { code?: string; message?: string; details?: Record<string, unknown> } | null;
      throw new HttpClientError(
        `upstream ${response.status}: ${body?.message ?? response.statusText}`,
        {
          code: body?.code ?? `HTTP_${response.status}`,
          status: response.status,
          details: body?.details ?? {},
        },
      );
    }

    return parsed as T;
  }
}

/**
 * services/paper `/risk/*` 客户端（ADR-0006 Step 5）。
 *
 * 包装 3 个 HTTP 端点：rules / locks / unlock。Mastra MCP tool 通过本 client
 * 暴露给 agent。`risk.unlock` 在 tool 层 `modelInvocable: false`（ADR-0011），
 * 但 client 不区分；权限隔离在 createTool 层做。
 */
import { HttpClient } from "./http.js";

export type RuleDescription = {
  name: string;
  short_desc: string;
};

export type RulesListResponse = {
  enabled: boolean;
  starting_balance: number;
  rules: RuleDescription[];
};

export type Lock = {
  id: number;
  scope: "global" | "market" | "symbol";
  market: string | null;
  symbol: string | null;
  side: "long" | "short" | "*";
  rule_name: string;
  reason: string;
  locked_at: string;
  locked_until: string;
};

export type LocksListResponse = {
  locks: Lock[];
};

export type ListLocksParams = {
  scope?: "global" | "market" | "symbol";
  market?: string;
  symbol?: string;
  limit?: number;
};

export type RiskClientOptions = {
  baseUrl: string;
  token: string;
  timeoutMs?: number;
};

export class RiskClient {
  private readonly http: HttpClient;

  constructor(options: RiskClientOptions) {
    this.http = new HttpClient(options);
  }

  /** GET /risk/rules —— describe 启动时加载的 rule 配置。 */
  async listRules(): Promise<RulesListResponse> {
    return await this.http.get<RulesListResponse>("/risk/rules");
  }

  /** GET /risk/locks —— 列 active locks，支持 scope / market / symbol 过滤。 */
  async listLocks(params: ListLocksParams = {}): Promise<LocksListResponse> {
    return await this.http.get<LocksListResponse>("/risk/locks", {
      scope: params.scope,
      market: params.market,
      symbol: params.symbol,
      limit: params.limit,
    });
  }

  /** POST /risk/locks/{id}/unlock —— 人工解锁（modelInvocable: false）。 */
  async unlock(lockId: number, reason: string): Promise<{ ok: boolean }> {
    return await this.http.post<{ ok: boolean }>(
      `/risk/locks/${lockId}/unlock`,
      { reason },
    );
  }
}

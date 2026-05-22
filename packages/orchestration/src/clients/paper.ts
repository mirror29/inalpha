/**
 * services/paper 客户端。
 */
import { HttpClient } from "./http.js";

export type PositionSnapshot = {
  instrument_id: string;
  quantity: number;
  avg_open_price: number;
  realized_pnl: number;
  generation: number;
};

export type BacktestReport = {
  strategy_id: string;
  venue: string;
  symbol: string;
  timeframe: string;
  initial_cash: number;
  final_equity: number;
  total_return_pct: number;
  num_trades: number;
  total_fees: number;
  num_bars_processed: number;
  period_start: string;
  period_end: string;
  final_positions: PositionSnapshot[];
};

export type BacktestParams = {
  strategyId: string;
  params?: Record<string, unknown>;
  venue?: string;
  symbol: string;
  timeframe?: string;
  fromTs: string;
  toTs: string;
  initialCash?: number;
  feeRate?: number;
};

export type SubmitOrderParams = {
  venue?: string;
  symbol: string;
  side: "BUY" | "SELL";
  type: "MARKET" | "LIMIT";
  quantity: number;
  price?: number;
  refPrice: number;
  feeRate?: number;
};

export type SubmitOrderResult = {
  client_order_id: string;
  venue: string;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT";
  requested_quantity: number;
  requested_price: number | null;
  status: "FILLED" | "REJECTED";
  filled_quantity: number;
  avg_fill_price: number | null;
  fee: number;
  notional: number;
  rejection_reason: string | null;
  ts_event: string;
};

export class PaperClient {
  private readonly http: HttpClient;

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
  }

  async health(): Promise<{ status: string; service: string; version: string }> {
    return await this.http.get("/health");
  }

  async listStrategies(): Promise<{ strategies: string[] }> {
    return await this.http.get("/strategies");
  }

  async runBacktest(params: BacktestParams): Promise<BacktestReport> {
    return await this.http.post<BacktestReport>("/backtest", {
      strategy_id: params.strategyId,
      params: params.params ?? {},
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      timeframe: params.timeframe ?? "1h",
      from_ts: params.fromTs,
      to_ts: params.toTs,
      initial_cash: params.initialCash ?? 10_000,
      fee_rate: params.feeRate ?? 0.001,
    });
  }

  async submitOrder(params: SubmitOrderParams): Promise<SubmitOrderResult> {
    return await this.http.post<SubmitOrderResult>("/orders/submit", {
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      side: params.side,
      type: params.type,
      quantity: params.quantity,
      price: params.price,
      ref_price: params.refPrice,
      fee_rate: params.feeRate ?? 0.001,
    });
  }
}

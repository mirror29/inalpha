/**
 * 第三道沙盒：返回值协议契约校验（ADR-0020）。
 *
 * sandbox 跑完代码后，stdout 可能是：
 *
 * - **raw 模式**（默认）：任意文本，不校验——给"算 1+1"类一次性脚本用
 * - **strategy_v1**：要求 stdout **是一行 JSON**，结构 = ``{version, signals[]}``，
 *   给 evolution loop / LLM 写策略用
 *
 * 把"strategy 协议"独立成 contract 而不是塞进 audit：契约关心**返回值结构**，
 * audit 关心**源码安全**——两个维度。
 *
 * **未来扩展**：factor_v1 / indicator_v1 / feature_v1 等可以平行加。
 */
import { z } from "zod";

// ────────────────────────────────────────────────────────────────────
// Contract schemas
// ────────────────────────────────────────────────────────────────────

/** Strategy v1 单条 signal：unix 时间戳 + 方向 + 数量。 */
export const StrategySignalSchema = z.object({
  /** unix 时间戳（毫秒）；不强制 ns/ms，调用方自己保持一致。 */
  ts: z.number().int(),
  side: z.enum(["BUY", "SELL"]),
  qty: z.number().positive(),
});

/** Strategy v1：``generate_signals(bars)`` 的标准返回结构。 */
export const StrategyV1Schema = z.object({
  version: z.literal("strategy_v1"),
  signals: z.array(StrategySignalSchema),
  /** 可选 metadata；evolution loop 可放调试信息 */
  metadata: z.record(z.string(), z.unknown()).optional(),
});

export type StrategyV1 = z.infer<typeof StrategyV1Schema>;
export type StrategySignal = z.infer<typeof StrategySignalSchema>;

/** Contract 选择。raw = 不校验；其它走对应 schema。 */
export const ContractKindSchema = z.enum(["raw", "strategy_v1"]);
export type ContractKind = z.infer<typeof ContractKindSchema>;

// ────────────────────────────────────────────────────────────────────
// 校验入口
// ────────────────────────────────────────────────────────────────────

export type ContractVerifyResult = {
  /** ok=true 表示通过；raw 模式始终 ok=true。 */
  ok: boolean;
  /** 失败原因列表（JSON 解析失败 / schema 校验失败）。 */
  errors: readonly string[];
  /** 通过 schema 校验后的 parsed object；raw 模式 / 失败时为 undefined。 */
  parsed?: unknown;
};

/**
 * 对 sandbox stdout 跑契约校验。
 *
 * @param kind   契约类型
 * @param stdout sandbox 进程的 stdout 文本
 */
export function verifyContract(kind: ContractKind, stdout: string): ContractVerifyResult {
  if (kind === "raw") {
    return { ok: true, errors: [] };
  }

  // 非 raw 都要求 stdout 是 JSON
  const trimmed = stdout.trim();
  if (!trimmed) {
    return { ok: false, errors: ["contract requires JSON stdout, got empty"] };
  }

  let json: unknown;
  try {
    json = JSON.parse(trimmed);
  } catch (err) {
    return {
      ok: false,
      errors: [`stdout is not valid JSON: ${(err as Error).message}`],
    };
  }

  if (kind === "strategy_v1") {
    const parsed = StrategyV1Schema.safeParse(json);
    if (!parsed.success) {
      return {
        ok: false,
        errors: parsed.error.issues.map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`),
      };
    }
    return { ok: true, errors: [], parsed: parsed.data };
  }

  // exhaustiveness guard
  const _exhaustive: never = kind;
  return { ok: false, errors: [`unknown contract kind: ${_exhaustive as string}`] };
}

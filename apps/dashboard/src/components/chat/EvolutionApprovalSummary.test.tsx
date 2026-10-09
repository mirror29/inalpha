import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";
import { EvolutionApprovalSummary } from "./EvolutionApprovalSummary";
import { evolutionApprovalSummary } from "@/lib/evolution-approval-summary";

let locale: "en" | "zh" = "zh";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string, values: Record<string, unknown> = {}) => {
  const dictionary = (locale === "zh" ? zh : en).activity.approval.preparation;
  const message = key.split(".").reduce<unknown>((current, part) => (current as Record<string, unknown>)[part], dictionary);
  return String(message).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? name));
} }));

const envelope = () => ({
  toolInput: { request: { seedStrategyId: "candidate:owned", budget: 4,
    config: { venue: "binance", symbol: "BTC/USDT", timeframe: "1h", trading_mode: "spot", from_ts: "2026-01-01T00:00:00Z", as_of: "2026-07-01T00:00:00Z",
      initial_cash: 10000, fee_rate: 0.001, leverage: 1, validation_split: 0.3 } },
    llm_snapshot: { provider: "deepseek", model: "deepseek-flash", api_key: "NEVER_SHOW_KEY" } },
  preparation: { seed_strategy_id: "candidate:owned", seed_context: { seed_label: "趋势过滤策略" }, seed_source_hash: "a".repeat(64), request_digest: "b".repeat(64),
    estimated_max_cost_usd: 0.08, dataset_manifest: { bar_count: 4320, latest_bar_ts: "2026-06-30T23:00:00Z", content_sha256: "c".repeat(64), warnings: [] } },
});

describe("readable frozen approval summary", () => {
  it("renders the strategy, market, window and cost in both languages before diagnostics", () => {
    for (const language of ["zh", "en"] as const) {
      locale = language;
      const html = renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: envelope() }));
      expect(html).toContain("趋势过滤策略");
      expect(html).toContain("BTC/USDT");
      expect(html).toContain("4320");
      expect(html).toContain("UTC");
      expect(html).toContain("$0.0800");
      const main = html.slice(0, html.indexOf("<details"));
      expect(main).not.toContain("a".repeat(64));
      expect(html).not.toContain("NEVER_SHOW_KEY");
      expect(html).toContain(language === "zh" ? "直接提交" : "submits this frozen task");
    }
  });
  it("explains the inherited evaluator training fraction and remaining validation window", () => {
    for (const language of ["zh", "en"] as const) {
      locale = language;
      const html = renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: envelope() }));
      expect(html).toContain(language === "zh" ? "前 30% 为训练，后 70% 为窗口内验证" : "first 30% is training; the remaining 70% is within-window validation");
    }
  });
  it("shows validation as disabled for a zero split and unavailable for invalid splits", () => {
    for (const language of ["zh", "en"] as const) {
      locale = language;
      const value = envelope();
      value.toolInput.request.config.validation_split = 0;
      let html = renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: value }));
      expect(html).toContain(language === "zh" ? "未启用窗口内验证" : "Within-window validation is disabled");
      expect(html).not.toContain("100%");
      for (const split of [Number.NaN, 0.6]) {
        value.toolInput.request.config.validation_split = split;
        html = renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: value }));
        expect(html).not.toContain(language === "zh" ? "为训练" : "is training");
      }
    }
  });
  it("does not invent metadata for old generic approvals", () => {
    expect(evolutionApprovalSummary({ requiresApproval: true, toolInput: {} })).toBeNull();
    expect(renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: {} }))).toBe("");
  });
  it("keeps invalid counts and missing costs unavailable and escapes seed labels", () => {
    const value = envelope();
    value.preparation.seed_context.seed_label = "<script>do-not-execute</script>";
    value.toolInput.request.budget = 1.5;
    value.preparation.dataset_manifest.bar_count = 0;
    delete (value.preparation as Partial<typeof value.preparation>).estimated_max_cost_usd;
    const summary = evolutionApprovalSummary(value)!;
    expect(summary.budget).toBeNull();
    expect(summary.bars).toBeNull();
    expect(summary.estimatedCost).toBeNull();
    const html = renderToStaticMarkup(createElement(EvolutionApprovalSummary, { envelope: value }));
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("$0.0000");
  });
});

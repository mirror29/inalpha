import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";
import type { EvolutionRun } from "@/lib/types";
import { EvolutionComparison } from "./EvolutionComparison";

let locale: "en" | "zh" = "zh";
vi.mock("next-intl", () => ({
  useTranslations:
    () =>
    (key: string, values: Record<string, unknown> = {}) => {
      const messages = locale === "zh" ? zh : en;
      const value = key
        .split(".")
        .reduce<unknown>(
          (current, part) => (current as Record<string, unknown>)[part],
          messages.evolution.comparison,
        );
      return String(value).replace(/\{(\w+)\}/g, (_, name) =>
        String(values[name] ?? name),
      );
    },
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/ui/Panel", () => ({
  Panel: ({
    title,
    children,
  }: {
    title: string;
    children: React.ReactNode;
  }) => (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  ),
}));

const snapshot = (fitness: number) => ({
  schema_version: "e1.report.v1",
  fitness,
  period_start: "2025-01-01",
  period_end: "2025-02-01",
  num_bars: 744,
  initial_cash: 10000,
  annualization_periods: 8760,
  total_return_pct: 5,
  total_fees: 12.5,
  num_trades: 0,
});
const render = (status = "completed", mode = "spot") =>
  renderToStaticMarkup(
    createElement(EvolutionComparison, {
      run: {
        run_id: "run",
        seed_strategy_id: "seed",
        budget: 4,
        llm_snapshot: null,
        llm_config_digest: null,
        active_stage: "completed",
        llm_cost_usd: 0.02,
        queued_at: "2026-10-09",
        started_at: null,
        finished_at: null,
        dataset_manifest: null,
        failure_code: null,
        failure_message: null,
        attempted: 1,
        succeeded: 1,
        rejected: 0,
        status,
        config: { trading_mode: mode, fee_rate: 0.001 },
        seed_report_snapshot: snapshot(0.2),
        baseline_snapshot: snapshot(0.3),
        candidates: [
          {
            candidate_id: "best",
            slot: 0,
            outcome: "succeeded",
            fitness: 0.5,
            run_id: "run",
            generation: 1,
            stage: "completed",
            source_code: null,
            source_hash: null,
            unified_diff: null,
            mutation_hint: null,
            llm_cost_usd: 0.02,
            audit_snapshot: null,
            contract_snapshot: null,
            error_code: null,
            error_message: null,
            created_at: null,
            updated_at: null,
            overfitting_risk: "high",
            evaluation_snapshot: snapshot(0.5),
          },
        ],
      } as EvolutionRun,
    }),
  );

describe("EvolutionComparison readable evidence", () => {
  it("shows Chinese full-window comparison and keeps adoption unproven", () => {
    locale = "zh";
    const html = render();
    expect(html).toContain("最佳候选的适应度高于原策略");
    expect(html).toContain("尚不具备采用证据");
    expect(html).toContain('href="/evolution/candidates/best"');
    expect(html).toContain("0.100%");
    expect(html).toContain("缺少时间切分检查");
    expect(html).not.toContain("undefined");
  });
  it("renders English and marks incomplete runs as provisional", () => {
    locale = "en";
    const html = render("running");
    expect(html).toContain("results are provisional");
    expect(html).toContain("Original strategy");
    expect(html).toContain("Trading fees (cash denomination)");
  });
  it("warns that perpetual execution differs from the spot benchmark", () => {
    locale = "zh";
    expect(render("completed", "perp")).toContain("仅供参考");
  });
});

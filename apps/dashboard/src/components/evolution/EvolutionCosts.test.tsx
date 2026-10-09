import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { EvolutionRun, EvolutionCandidateSummary } from "@/lib/types";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";
import { EvolutionCandidateDetailClient } from "./EvolutionCandidateDetailClient";
import { EvolutionCosts } from "./EvolutionCosts";
import { EvolutionCandidates } from "./EvolutionCandidates";

let locale: "en" | "zh" = "zh";
let detailCandidate: EvolutionCandidateSummary;
vi.mock("swr", () => ({
  default: () => ({
    data: { candidate: detailCandidate, asOf: "2026-10-09" },
    isLoading: false,
    isValidating: false,
    mutate: () => {},
  }),
}));
vi.mock("@/components/ui/LiveStrip", () => ({ LiveStrip: () => null }));
vi.mock("next-intl", () => ({
  useTranslations: (namespace: string) => {
    const messages = locale === "zh" ? zh : en;
    const dictionary = namespace
      .split(".")
      .reduce<unknown>(
        (value, key) => (value as Record<string, unknown>)[key],
        messages,
      ) as Record<string, unknown>;
    const translate = (key: string, values: Record<string, unknown> = {}) => {
      const text = key
        .split(".")
        .reduce<unknown>(
          (value, part) => (value as Record<string, unknown>)[part],
          dictionary,
        );
      return String(text).replace(/\{(\w+)\}/g, (_, name) =>
        String(values[name] ?? name),
      );
    };
    translate.has = (key: string) =>
      key
        .split(".")
        .reduce<unknown>(
          (value, part) => value && (value as Record<string, unknown>)[part],
          dictionary,
        ) !== undefined;
    return translate;
  },
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/ui/Panel", () => ({
  Panel: ({ children }: { children: React.ReactNode }) => (
    <section>{children}</section>
  ),
}));
vi.mock("@/components/ui/StatusBadge", () => ({
  StatusBadge: ({ label }: { label: string }) => <span>{label}</span>,
}));

const candidates = [
  {
    candidate_id: "failed",
    slot: 0,
    outcome: "diff_failed",
    stage: "completed",
    llm_cost_usd: null,
    usage_status: "unknown",
  },
  {
    candidate_id: "repair",
    slot: 1,
    parent_id: "failed",
    outcome: "succeeded",
    stage: "completed",
    llm_cost_usd: 0.01,
    usage_status: "known",
    fitness: 0.3,
  },
] as EvolutionCandidateSummary[];

describe("evolution cost and repair evidence", () => {
  it("candidate detail keeps unknown tokens unavailable and links its repair parent", () => {
    locale = "zh";
    detailCandidate = {
      candidate_id: "repair",
      run_id: "run",
      parent_id: "failed",
      slot: 1,
      stage: "completed",
      outcome: "mutation_failed",
      usage_status: "unknown",
      llm_cost_usd: null,
      input_tokens: 0,
      output_tokens: 0,
      overfitting_risk: "high",
    } as EvolutionCandidateSummary;
    const html = renderToStaticMarkup(
      createElement(EvolutionCandidateDetailClient, { candidateId: "repair" }),
    );
    expect(html).toContain('href="/evolution/candidates/failed"');
    expect(html).toContain("用量待确认");
    expect(html).toContain("输入 token: —");
    expect(html).toContain("输出 token: —");
    expect(html).not.toContain("$0.0000");
  });

  it("localizes unknown calls and links the failed parent without inventing zero cost", () => {
    locale = "zh";
    const html = renderToStaticMarkup(
      createElement(EvolutionCandidates, { candidates }),
    );
    expect(html).toContain("用量待确认");
    expect(html).toContain("修复自候选 #1");
    expect(html).toContain('href="/evolution/candidates/failed"');
    expect(html).toContain("$0.0100");
    expect(html).not.toContain("$0.0000");
    expect(html).not.toContain(">diff_failed<");
  });
  it("separates English receipts, recorded amounts and unassigned chat costs", () => {
    locale = "en";
    const run = {
      run_id: "run",
      candidates,
      llm_cost_usd: 0.03,
      known_cost_usd: 0.01,
      unknown_usage_count: 1,
    } as EvolutionRun;
    const html = renderToStaticMarkup(createElement(EvolutionCosts, { run }));
    expect(html).toContain("Confirmed usage subtotal");
    expect(html).toContain("$0.0300");
    expect(html).toContain("not separate charges");
    expect(html).toContain("No trusted turn association is available");
    expect(html).not.toContain("undefined");
  });
  it("shows the linked preparation turn separately and warns against shared cost double counting", () => {
    locale = "zh";
    const run = { candidates, llm_cost_usd: 0.03, chat_preparation_costs: {
      linked: true, call_count: 2, known_cost_usd: 0.0000015,
      unknown_cost_count: 1, shared_approval_count: 2,
    } } as EvolutionRun;
    const html = renderToStaticMarkup(createElement(EvolutionCosts, { run }));
    expect(html).toContain("审批准备会话费用");
    expect(html).toContain("本轮 2 次模型调用");
    expect(html).toContain("未知金额 1 次");
    expect(html).toContain("不能逐任务累加");
    expect(html).toContain("&lt;$0.0001");
  });
  it("does not invent a zero amount for linked calls with unknown pricing", () => {
    locale = "en";
    const run = { candidates: [], chat_preparation_costs: {
      linked: true, call_count: 1, known_cost_usd: null,
      unknown_cost_count: 1, shared_approval_count: 1,
    } } as unknown as EvolutionRun;
    const html = renderToStaticMarkup(createElement(EvolutionCosts, { run }));
    expect(html).toContain("calls with unknown cost: 1");
    expect(html).toContain("known estimate Unconfirmed");
    expect(html.slice(html.indexOf("Approval preparation turn cost"))).not.toContain("$0.0000");
    expect(html).not.toContain("undefined");
  });

});

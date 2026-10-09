import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { EvolutionLineage as Lineage } from "@/lib/types";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh.json";
import { EvolutionHypothesisLineage } from "./EvolutionHypothesisLineage";
import type { EvolutionHypothesis } from "@/lib/types";
import { EvolutionLineage } from "./EvolutionLineage";

let locale: "zh" | "en" = "zh";
vi.mock("@/components/ui/Panel", () => ({
  Panel: ({ title, children }: { title: string; children: unknown }) =>
    createElement("section", null, title, children as never),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children }: { href: string; children: unknown }) =>
    createElement("a", { href }, children as never),
}));
vi.mock("next-intl", () => ({
  useTranslations: (namespace: string) => {
    const messages = locale === "zh" ? zh : en;
    const value = (key: string): unknown =>
      `${namespace}.${key}`
        .split(".")
        .reduce<unknown>(
          (item, field) => (item as Record<string, unknown>)?.[field],
          messages,
        );
    const translate = (key: string, args: Record<string, unknown> = {}) =>
      String(value(key)).replace(/\{(\w+)\}/g, (_, name) =>
        String(args[name] ?? name),
      );
    translate.has = (key: string) => value(key) !== undefined;
    return translate;
  },
}));

const graph: Lineage = {
  owner_account_id: "owner-private",
  source_reference: "candidate:source-private",
  experiment_id: "root-private",
  current_run_id: "retry-private",
  display_origin: {
    description: "Readable <source>",
    href: "/lab/source-private",
  },
  attempts: [
    {
      run_id: "root-private",
      attempt_number: 1,
      retry_of_run_id: null,
      status: "failed",
    },
    {
      run_id: "retry-private",
      attempt_number: 2,
      retry_of_run_id: "root-private",
      status: "completed",
    },
  ],
  loops: [],
  campaigns: [],
};

it("renders readable origin and retry links with private IDs confined to URLs", () => {
  locale = "zh";
  const html = renderToStaticMarkup(
    createElement(EvolutionLineage, { lineage: graph }),
  );
  expect(html).toContain("第 2 次尝试");
  expect(html).toContain("评估已完成");
  expect(html).toContain('href="/evolution/root-private"');
  expect(html).toContain('aria-current="step"');
  expect(html).toContain("Readable &lt;source&gt;");
  expect(html.replace(/href="[^"]*"/g, "")).not.toMatch(
    /root-private|retry-private|owner-private|candidate:source-private/,
  );
  expect(html).toContain("单代评估完成不代表已进入 Forward");
});

it("keeps champion, Forward, sealed results and human records distinct", () => {
  locale = "en";
  const lineage = {
    ...graph,
    campaigns: [
      {
        campaign_id: "search-private",
        source_run_id: "retry-private",
        status: "waiting_forward",
        locked_candidate_id: "champion-private",
        forward_sandbox_id: "forward-private",
        forward_status: "insufficient_evidence",
        forward_event_count: 1,
        holdout_attempt_id: null,
        holdout_status: null,
        holdout_passed: null,
        adoption_count: 0,
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(EvolutionLineage, { lineage }),
  );
  expect(html).toContain("One candidate locked");
  expect(html).toContain("Insufficient Forward evidence");
  expect(html).toContain("Not consumed");
  expect(html).toContain("0 adoption records");
  expect(html).not.toContain("Test passed");
  expect(html).not.toContain("champion-private");
});

it("marks missing lineage explicitly and does not fabricate a successful observation", () => {
  locale = "zh";
  const html = renderToStaticMarkup(
    createElement(EvolutionLineage, { lineage: null }),
  );
  expect(html).toContain("不据此推断实验没有结果");
  expect(html).not.toContain("检验通过");
});

it("shows hypothesis ancestry with readable generation labels and marks missing parents", () => {
  locale = "zh";
  const parent = {
    hypothesis_id: "parent-hash",
    campaign_id: "owned",
    generation: 1,
    slot: 2,
  } as EvolutionHypothesis;
  const child = {
    hypothesis_id: "child-hash",
    campaign_id: "owned",
    generation: 2,
    slot: 0,
    lineage_kind: "mutation",
    parent_ids: ["parent-hash", "missing-hash"],
  } as EvolutionHypothesis;
  const html = renderToStaticMarkup(
    createElement(EvolutionHypothesisLineage, {
      hypothesis: child,
      hypotheses: [parent, child],
      onInspect: () => {},
    }),
  );
  expect(html).toContain("从既有假设变异");
  expect(html).toContain("查看第 1 代假设 3");
  expect(html).toContain("部分父假设记录缺失");
  expect(html).not.toMatch(/parent-hash|child-hash|missing-hash/);
});

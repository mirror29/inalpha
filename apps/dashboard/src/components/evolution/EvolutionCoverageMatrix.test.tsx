import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CoverageMatrixResults, type CoverageMatrix } from "./EvolutionCoverageMatrix";

vi.mock("@/i18n/navigation", () => ({ Link: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

/** A comparison guides preparation without granting execution authority. */
describe("coverage matrix presentation", () => {
  const matrix: CoverageMatrix = {
    complete: true, sampled_at: "2026-10-13T01:00:00Z", recommended_target_id: "btc",
    rows: [
      { asset: "BTC", target_id: "btc", status: "necessary_inputs_present", independent_event_upper_bound: 8, minimum_matched_event_pairs: 8, preparation: { selection_fact_count: 22, dataset_manifest: { latest_bar_ts: "2026-10-13T00:00:00Z", cutoff_bar_ts: "2026-10-13T00:00:00Z", freshness_lag_seconds: 0, warnings: ["dataset warning remains visible"] } } },
      { asset: "SOL", target_id: "sol", status: "blocked", independent_event_upper_bound: 2, minimum_matched_event_pairs: 8, blocker_codes: ["E2_INPUT_COVERAGE_INSUFFICIENT"] },
    ],
  };
  it.each([true, false])("separates preparation from evaluation (zh=%s)", zh => {
    const html = renderToStaticMarkup(createElement(CoverageMatrixResults, { matrix, zh }));
    expect(html).toContain(zh ? "尚未正式评估" : "evaluation unverified");
    expect(html).toContain(zh ? "本轮优先准备" : "First preparation choice");
    expect(html).toContain("targetId=btc");
    expect(html).toContain(zh ? "选择段独立事件上界不足" : "Selection independent-event upper bound is insufficient");
    expect(html).not.toContain("<button");
    expect(html).toContain("dataset warning remains visible");
  });
  it("explains partial failure and withholds a recommendation", () => {
    const html = renderToStaticMarkup(createElement(CoverageMatrixResults, { matrix: { ...matrix, complete: false, recommended_target_id: null }, zh: true }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("本轮不推荐首选实验");
    expect(html).not.toContain("本轮优先准备");
  });
  it("explains inventory limits without suggesting a login failure", () => {
    const html = renderToStaticMarkup(createElement(CoverageMatrixResults, { matrix: { ...matrix, complete: false, recommended_target_id: null, rows: [{ asset: "BTC", target_id: null, status: "unavailable", blocker_codes: ["RUNNER_INVENTORY_INCOMPLETE"] }] }, zh: true }));
    expect(html).toContain("运行任务列表达到盘点上限");
    expect(html).toContain("从策略详情逐项准备");
  });

});

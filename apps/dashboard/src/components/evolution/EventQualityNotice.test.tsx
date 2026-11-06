import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EventQualityNotice } from "./EventQualityNotice";

/** Evidence warnings must remain actionable in both locales, without claiming evaluation passed. */
describe("event quality notice", () => {
  it.each([true, false])("explains retained legacy facts and provenance gaps (zh=%s)", zh => {
    const html = renderToStaticMarkup(createElement(EventQualityNotice, { zh, quality: {
      status: "review_required", inspected_fact_count: 25, legacy_regulatory_fact_count: 3,
      unverified_provenance_fact_count: 1, extractor_version_counts: { "deterministic-event-extractor-v2": 25 },
    } }));
    expect(html).toContain('role="alert"');
    expect(html).toContain(zh ? "暂不能启动搜索" : "search cannot start");
    expect(html).toContain(zh ? "历史事实和原始数量统计保留" : "Historical facts and raw counts are retained");
    expect(html).toContain(zh ? "来源版本尚未确认" : "unverified extractor provenance");
    expect(html).toContain("deterministic-event-extractor-v2");
  });
  it("keeps a clean known-defect check distinct from formal evaluation", () => {
    const html = renderToStaticMarkup(createElement(EventQualityNotice, { zh: false, quality: { status: "no_known_legacy_defect", inspected_fact_count: 8 } }));
    expect(html).not.toContain('role="alert"');
    expect(html).toContain("semantics, matched controls and FDR remain unverified");
    expect(html).toContain("listing, SEC and other classifications are not audited");
  });
  it("does not invent a check for an old response", () => {
    expect(renderToStaticMarkup(createElement(EventQualityNotice, { zh: true }))).toBe("");
  });
  it("honors an explicit blocker even with an unexpected status", () => {
    const html = renderToStaticMarkup(createElement(EventQualityNotice, { zh: true, quality: {
      status: "not_checked", blocker_codes: ["E2_EVENT_PROVENANCE_UNVERIFIED"],
    } }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("暂不能启动搜索");
  });
});

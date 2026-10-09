import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EvolutionClient } from "./EvolutionClient";

const state = vi.hoisted(() => ({
  loops: {} as Record<string, unknown>,
  campaigns: {} as Record<string, unknown>,
  runs: {} as Record<string, unknown>,
}));
vi.mock("swr", () => ({ default: (key: string) => key.endsWith("loops") ? state.loops : state.campaigns }));
vi.mock("@/lib/use-evolution-runs", () => ({ useEvolutionRuns: () => state.runs }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/i18n/navigation", () => ({ Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }));
vi.mock("@/components/ui/LiveStrip", () => ({ LiveStrip: () => null }));
vi.mock("./EvolutionLoopTable", () => ({ EvolutionLoopTable: () => <section>E2 workflows</section> }));
vi.mock("./EvolutionCampaignTable", () => ({ EvolutionCampaignTable: () => <section>Independent campaigns</section> }));
vi.mock("./EvolutionRunTable", () => ({ EvolutionRunTable: ({ runs, hasMore }: { runs: { run_id: string }[]; hasMore: boolean }) => <section>{runs.map((r) => <span key={r.run_id}>{r.run_id}</span>)}{hasMore && <button>Load more</button>}</section> }));

/** Reproduce completed E1 results hidden by empty or unavailable E2 sources. */
describe("EvolutionClient result visibility", () => {
  beforeEach(() => {
    state.loops = { data: { loops: [], asOf: "2026-10-09T06:00:00Z" } };
    state.campaigns = { data: { campaigns: [] } };
    state.runs = { runs: [{ run_id: "BTC completed", status: "completed" }], data: [{}], hasMore: true };
  });
  it("shows E1 results and pagination without a collapsed history gate", () => {
    const html = renderToStaticMarkup(createElement(EvolutionClient));
    expect(html).toContain("BTC completed");
    expect(html).toContain("Load more");
    expect(html).not.toContain("<details");
    expect(html).not.toContain("E2 workflows");
    expect(html).toMatch(/workflow.loadedTasks<\/p><p[^>]*>1<\/p>/);
  });
  it.each(["loading", "error"])("retains loaded E1 when E2 is %s", (mode) => {
    state.loops = mode === "loading" ? { isLoading: true } : { error: new Error("offline") };
    state.campaigns = { error: new Error("offline") };
    const html = renderToStaticMarkup(createElement(EvolutionClient));
    expect(html).toContain("BTC completed");
    expect(html).toContain("workflow.partial");
  });
  it("keeps E2 visible when E1 fails", () => {
    state.loops = { data: { loops: [{ status: "waiting_forward" }] } };
    state.runs = { runs: [], error: new Error("offline") };
    expect(renderToStaticMarkup(createElement(EvolutionClient))).toContain("E2 workflows");
  });
});

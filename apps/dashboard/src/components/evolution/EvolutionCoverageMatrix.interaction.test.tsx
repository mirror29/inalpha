// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvolutionCoverageMatrix } from "./EvolutionCoverageMatrix";

vi.mock("next-intl", () => ({ useLocale: () => "en" }));
vi.mock("@/i18n/navigation", () => ({ Link: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

let container: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn();
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<EvolutionCoverageMatrix />));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

/** Exercise actual button state, transport and retry visibility through React DOM. */
describe("coverage inventory interaction", () => {
  it("posts the free mode, prevents pending clicks, and clears stale results before retry", async () => {
    const result = { complete: true, sampled_at: "2026-10-13T01:00:00Z", recommended_target_id: "btc", rows: [{ asset: "BTC", target_id: "btc", status: "necessary_inputs_present", minimum_matched_event_pairs: 8, independent_event_upper_bound: 9 }] };
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => result });
    await act(async () => container.querySelector("button")!.click());
    expect(fetchMock).toHaveBeenCalledWith("/api/evolution/prepare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "coverage_matrix" }) });
    expect(container.textContent).toContain("First preparation choice");
    let finish!: (response: { ok: boolean }) => void;
    fetchMock.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await act(async () => container.querySelector("button")!.click());
    expect(container.querySelector("button")!.disabled).toBe(true);
    expect(container.textContent).not.toContain("First preparation choice");
    await act(async () => container.querySelector("button")!.click());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => finish({ ok: false }));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Coverage check unavailable");
    expect(container.querySelector("button")!.disabled).toBe(false);
  });
  it("shows a safe failure message for a rejected transport without leaking details", async () => {
    fetchMock.mockRejectedValueOnce(new Error("private upstream details"));
    await act(async () => container.querySelector("button")!.click());
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).not.toContain("private upstream details");
    expect(container.querySelector("button")!.disabled).toBe(false);
  });
});

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { EvolutionRun } from "@/lib/types";
import { EvolutionRunHeader } from "./EvolutionRunHeader";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/i18n/navigation", () => ({ Link: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
vi.mock("@/components/ui/LiveStrip", () => ({ LiveStrip: () => null }));
vi.mock("@/components/ui/StatusBadge", () => ({ StatusBadge: () => null }));

/** Only a server-approved standalone terminal task offers a retry; attempts retain lineage. */
describe("EvolutionRunHeader retries", () => {
  const render = (run: Partial<EvolutionRun>) => renderToStaticMarkup(createElement(EvolutionRunHeader, {
    run: { run_id: "owned", config: {}, status: "failed", ...run } as EvolutionRun,
    asOf: "2026-10-09T07:00:00Z", isValidating: false, isStaleFrame: false, onAbort: () => {},
  }));
  it("offers retry only when the server permits it", () => {
    expect(render({ retry_allowed: true })).toContain(">retry</button>");
    expect(render({ retry_allowed: false })).not.toContain(">retry</button>");
    expect(render({ status: "running", retry_allowed: true })).not.toContain(">retry</button>");
  });
  it("shows the previous attempt without replacing its result", () => {
    const html = render({ attempt_number: 2, retry_of_run_id: "previous" });
    expect(html).toContain("retryAttempt");
    expect(html).toContain('href="/evolution/previous"');
  });
});

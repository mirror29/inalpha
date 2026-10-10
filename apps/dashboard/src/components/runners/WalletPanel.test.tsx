import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { RunWalletPayload, StrategyRunRecord } from "@/lib/types";
import { WalletPanel } from "./WalletPanel";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const run = {
  id: "wallet-ui-fixture",
  status: "stopped",
  accounting_status: "verified",
  cumulative_pnl: 12,
} as StrategyRunRecord;
const data: RunWalletPayload = {
  wallet: {
    cash_balances: { USDT: 100 },
    initial_cash: 100,
    equity: 100,
    net_pnl: 0,
    valuation_at: null,
    released_at: null,
    warnings: [],
  },
};

/** Render presentation contracts without submitting any runner or capital action. */
function render(
  record: StrategyRunRecord,
  wallet: RunWalletPayload | null,
  flat: boolean,
) {
  return renderToStaticMarkup(
    <WalletPanel run={record} data={wallet} flat={flat} refresh={() => {}} />,
  );
}

describe("wallet presentation boundaries", () => {
  it("keeps legacy audit evidence visible when no wallet snapshot exists", () => {
    const html = render(
      {
        ...run,
        accounting_status: "legacy_unverified",
        accounting_note: "historical balance mismatch",
        original_cumulative_pnl: 42,
      },
      null,
      true,
    );
    expect(html).toContain("legacyWarning");
    expect(html).toContain("historical balance mismatch");
    expect(html).toContain("original");
    expect(html).toContain("42");
  });

  it("does not offer resume or capital release for an active run", () => {
    const html = render({ ...run, status: "running" }, data, false);
    expect(html).not.toContain(">release<");
    expect(html).not.toContain(">resume<");
    expect(html).not.toContain("verified");
  });

  it("does not guess the cause of an arbitrary wallet warning", () => {
    const html = render(
      run,
      {
        wallet: {
          ...data.wallet!,
          warnings: ["Funding payment could not be confirmed"],
        },
      },
      true,
    );
    expect(html).toContain("warningSummary");
    expect(html).not.toContain("valuationWarning");
  });

  it("explains the remaining open-position requirement for a stopped run", () => {
    const html = render(run, data, false);
    expect(html).toContain("releaseRequiresClose");
    expect(html).not.toContain("releaseRequiresStopped");
    expect(html).toMatch(/disabled=""[^>]*>release<\/button>/);
  });
});

"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { RunWalletPayload, StrategyRunRecord } from "@/lib/types";

/** Show the book's own balances, valuation age, and explicit capital settlement. */
export function WalletPanel({ run, data, flat, refresh }: { run: StrategyRunRecord; data?: RunWalletPayload | null; flat: boolean; refresh: () => void }) {
  const t = useTranslations("walletAccounting");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const wallet = data?.wallet;
  async function action(operation: "release_capital" | "resume") {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/runners/${run.id}/${operation}`, { method: "POST" });
      if (!response.ok) throw new Error((await response.json()).error);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return <section className="rounded-xl border border-border-subtle p-4 text-sm">
    <p>{t(run.accounting_status ?? "legacy_unverified")}</p>
    {run.accounting_status !== "verified" && <p className="text-gold">{t("legacyWarning")} {run.accounting_note} {t("original")}: {run.original_cumulative_pnl ?? run.cumulative_pnl}</p>}
    {wallet ? <>
      <p>{t("cash")}: {Object.entries(wallet.cash_balances).map(([currency, amount]) => `${currency} ${Number(amount).toFixed(2)}`).join(" · ") || "0"}</p>
      <p>{t("equity")}: {wallet.equity == null ? "—" : Number(wallet.equity).toFixed(2)} · {t("asOf")}: {wallet.valuation_at ?? "—"}</p>
      {data?.pnl_components && <p>{t("fees")}: {Number(data.pnl_components.fees).toFixed(2)} · {t("funding")}: {Number(data.pnl_components.funding).toFixed(2)} · {t("realized")}: {Number(data.pnl_components.realized).toFixed(2)} · {t("unrealized")}: {data.pnl_components.unrealized == null ? "—" : Number(data.pnl_components.unrealized).toFixed(2)} {data.pnl_components.currency}</p>}
      {wallet.warnings.map((warning) => <p key={warning} className="text-gold">{warning}</p>)}
      {wallet.released_at ? <p>{t("released")}</p> : <>
        <p>{t("stopWarning")}</p>
        {run.status !== "running" && <button type="button" disabled={busy} onClick={() => action("resume")} className="mt-2 mr-2 rounded border border-border-subtle px-3 py-1 disabled:opacity-40">{t("resume")}</button>}
        <button type="button" disabled={busy || run.status !== "stopped" || !flat} onClick={() => action("release_capital")} className="mt-2 rounded border border-border-subtle px-3 py-1 disabled:opacity-40">{t("release")}</button>
      </>}
    </> : run.accounting_status === "verified" && <p className="text-gold">{t("unavailable")}</p>}
    {error && <p role="alert" className="text-fox-red">{error}</p>}
  </section>;
}

"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { RunWalletPayload, StrategyRunRecord } from "@/lib/types";
import { Button } from "@/components/ui/button";

/** Show the book's own balances, valuation age, and explicit capital settlement. */
export function WalletPanel({
  run,
  data,
  flat,
  refresh,
}: {
  run: StrategyRunRecord;
  data?: RunWalletPayload | null;
  flat: boolean;
  refresh: () => void;
}) {
  const t = useTranslations("walletAccounting");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const wallet = data?.wallet;
  const onlyValuationWarnings = wallet?.warnings.every(
    (warning) =>
      warning.includes(
        "price unavailable or stale; retaining last trusted valuation",
      ) ||
      warning ===
        "Wallet changed during valuation; retaining previous snapshot",
  );
  async function action(operation: "release_capital" | "resume") {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/runners/${run.id}/${operation}`, {
        method: "POST",
      });
      if (!response.ok) throw new Error((await response.json()).error);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="rounded-xl border border-border-subtle p-4 text-sm">
      {run.accounting_status !== "verified" && (
        <p className="text-gold">{t("legacyWarning")}</p>
      )}
      {wallet ? (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <p className="text-fg-muted">
              {t("cash")}{" "}
              <span className="tnum font-mono text-fg">
                {Object.entries(wallet.cash_balances)
                  .map(
                    ([currency, amount]) =>
                      `${currency} ${Number(amount).toFixed(2)}`,
                  )
                  .join(" · ") || "0"}
              </span>
            </p>
            <p className="text-fg-muted">
              {t("equity")}{" "}
              <span className="tnum font-mono text-fg">
                {wallet.equity == null ? "—" : Number(wallet.equity).toFixed(2)}
              </span>
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto"
              aria-expanded={showDetails}
              aria-controls="wallet-details"
              onClick={() => setShowDetails(!showDetails)}
            >
              {t(showDetails ? "hideDetails" : "details")}
            </Button>
          </div>
          {wallet.warnings.length > 0 && (
            <p className="mt-2 text-xs text-gold">
              {t(
                onlyValuationWarnings && wallet.equity !== null
                  ? "valuationWarning"
                  : "warningSummary",
              )}
            </p>
          )}
          {showDetails && (
            <div
              id="wallet-details"
              className="mt-3 space-y-1 border-t border-border-subtle pt-3 text-xs text-fg-muted"
            >
              <p>{t(run.accounting_status ?? "legacy_unverified")}</p>
              <p>
                {t("asOf")}: {wallet.valuation_at ?? "—"}
              </p>
              {data?.pnl_components && (
                <p>
                  {t("fees")}: {Number(data.pnl_components.fees).toFixed(2)} ·{" "}
                  {t("funding")}:{" "}
                  {Number(data.pnl_components.funding).toFixed(2)} ·{" "}
                  {t("realized")}:{" "}
                  {Number(data.pnl_components.realized).toFixed(2)} ·{" "}
                  {t("unrealized")}:{" "}
                  {data.pnl_components.unrealized == null
                    ? "—"
                    : Number(data.pnl_components.unrealized).toFixed(2)}{" "}
                  {data.pnl_components.currency}
                </p>
              )}
              {run.accounting_status !== "verified" && (
                <p>
                  {t("original")}:{" "}
                  {run.original_cumulative_pnl ?? run.cumulative_pnl}
                </p>
              )}
              {wallet.warnings.map((warning, index) => (
                <p
                  key={`${index}:${warning}`}
                  className="break-words text-gold"
                >
                  {warning}
                </p>
              ))}
            </div>
          )}
          {wallet.released_at ? (
            <p className="mt-2 text-xs text-fg-muted">{t("released")}</p>
          ) : (
            run.status !== "running" && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => action("resume")}
                >
                  {t("resume")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || run.status !== "stopped" || !flat}
                  onClick={() => action("release_capital")}
                >
                  {t("release")}
                </Button>
                <span className="text-xs text-fg-muted">
                  {t(
                    run.status !== "stopped"
                      ? "releaseRequiresFlat"
                      : !flat
                        ? "releaseRequiresClose"
                        : "releaseReady",
                  )}
                </span>
              </div>
            )
          )}
        </>
      ) : (
        run.accounting_status === "verified" && (
          <p className="text-gold">{t("unavailable")}</p>
        )
      )}
      {error && (
        <p role="alert" className="mt-2 text-fox-red">
          {error}
        </p>
      )}
    </section>
  );
}

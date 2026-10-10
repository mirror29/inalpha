/** Preserve the ledger's twelve decimal places, including sub-cent estimates. */
export function formatUsageCost(value: string): string {
  return `$${Number(value).toFixed(12).replace(/\.?0+$/, "") || "0"}`;
}

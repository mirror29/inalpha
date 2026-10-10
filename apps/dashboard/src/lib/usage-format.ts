/** Format the ledger decimal string without losing precision through binary floats. */
export function formatUsageCost(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const decimals = fraction.replace(/0+$/, "");
  return `$${whole}${decimals ? `.${decimals}` : ""}`;
}

const tokenFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

/** Compact database integer strings without passing through imprecise numbers. */
export function formatUsageTokens(value: string | null): string {
  return value !== null && /^\d+$/.test(value)
    ? tokenFormatter.format(BigInt(value))
    : "—";
}

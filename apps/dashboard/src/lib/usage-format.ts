/** Format the ledger decimal string without losing precision through binary floats. */
export function formatUsageCost(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const decimals = fraction.replace(/0+$/, "");
  return `$${whole}${decimals ? `.${decimals}` : ""}`;
}

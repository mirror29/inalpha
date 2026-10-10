import { describe, expect, it } from "vitest";
import { formatUsageCost } from "./usage-format";

describe("formatUsageCost", () => {
  it("keeps nonzero costs at the ledger's minimum precision visible", () => {
    expect(formatUsageCost("0.000000000001")).toBe("$0.000000000001");
    expect(formatUsageCost("0.000000001000")).toBe("$0.000000001");
    expect(formatUsageCost("0.000000000000")).toBe("$0");
    expect(formatUsageCost("12345678.123456789012")).toBe("$12345678.123456789012");
    expect(formatUsageCost("10000000.000000000000")).toBe("$10000000");
    expect(formatUsageCost("10.500000000000")).toBe("$10.5");
  });
});

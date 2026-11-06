import { describe, expect, it } from "vitest";
import { formatUsageTokens } from "./usage-format";

describe("compact usage tokens", () => {
  it("keeps unknown usage distinct from known zero", () => {
    expect(formatUsageTokens(null)).toBe("—");
    expect(formatUsageTokens("0")).toBe("0");
  });

  it("uses compact units for long totals and promotes rounded boundaries", () => {
    expect(formatUsageTokens("999")).toBe("999");
    expect(formatUsageTokens("19421")).toBe("19.42K");
    expect(formatUsageTokens("5808")).toBe("5.81K");
    expect(formatUsageTokens("999999")).toBe("1M");
    expect(formatUsageTokens("1234567890")).toBe("1.23B");
  });

  it("treats invalid integer strings as unknown instead of crashing", () => {
    for (const value of ["", " ", "12.5", "1_000", "0x10", "unknown", "-1"]) {
      expect(formatUsageTokens(value)).toBe("—");
    }
  });

  it("supports database integers above the JavaScript safe integer limit", () => {
    expect(formatUsageTokens("9223372036854775807")).toBe("9,223,372.04T");
  });
});

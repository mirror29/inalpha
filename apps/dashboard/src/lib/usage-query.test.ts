import { describe, expect, it } from "vitest";
import { usageFilter } from "./usage-query";

describe("usage filters", () => {
  it("keeps owner immutable and SQL values parameterized", () => {
    const result = usageFilter(new URLSearchParams({ auth_sub: "bob", model: "' OR TRUE--", arm_id: "D" }), "alice");
    expect(result.values).toEqual(["alice", "' OR TRUE--", "D"]);
    expect(result.where).not.toContain("OR TRUE");
    expect(result.where).toContain("auth_sub=$1");
  });
  it("rejects unsafe pagination and malformed dates", () => {
    for (const query of ["offset=-1", "offset=NaN", "offset=0.5", "from=nonsense"]) {
      expect(() => usageFilter(new URLSearchParams(query), "alice")).toThrow();
    }
  });
});

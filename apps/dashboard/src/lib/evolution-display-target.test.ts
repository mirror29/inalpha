import { beforeEach, describe, expect, it, vi } from "vitest";
const backend = vi.hoisted(() => vi.fn());
vi.mock("./backend", () => ({ backendFetch: backend }));
import {
  withDisplayTarget,
  withLineageOrigin,
} from "./evolution-display-target";
import type { EvolutionLoop, EvolutionLineage } from "./types";
const id = "11111111-1111-4111-8111-111111111111";
const loop = {
  owner_account_id: id,
  target_kind: "strategy_candidate",
  target_id: id,
} as EvolutionLoop;

/** Missing or inaccessible targets must not expose foreign data or hide owned research. */
describe("owner-scoped readable evolution targets", () => {
  beforeEach(() => {
    backend.mockReset();
  });
  it("uses the authenticated backend description and a stable strategy URL", async () => {
    backend.mockResolvedValue({
      description: "Event confirmation strategy",
      owner_account_id: id,
    });
    const result = await withDisplayTarget(loop);
    expect(result.display_target).toEqual({
      description: "Event confirmation strategy",
      href: `/lab/${id}`,
    });
    expect(backend).toHaveBeenCalledWith(
      "paper",
      `/strategy_candidates/${id}`,
      { timeoutMs: 2000 },
    );
  });
  it.each([null, "22222222-2222-4222-8222-222222222222"])(
    "hides descriptions for an unowned candidate (%s)",
    async (owner) => {
      backend.mockResolvedValue({
        description: "Foreign strategy",
        owner_account_id: owner,
      });
      expect((await withDisplayTarget(loop)).display_target).toBeNull();
    },
  );
  it("retains an owned loop when the target has been deleted or access is denied", async () => {
    backend.mockImplementation(async () => {
      throw new Error("404");
    });
    const observed = await withDisplayTarget(loop);
    expect(observed).toEqual({ ...loop, display_target: null });
  });
  it("does not construct backend paths from malformed target references", async () => {
    const malformed = { ...loop, target_id: "../other-owner" };
    expect(await withDisplayTarget(malformed)).toBe(malformed);
    expect(backend).not.toHaveBeenCalled();
  });
});

it("enriches a lineage origin only after source ownership matches", async () => {
  backend.mockReset();
  const lineage = {
    owner_account_id: id,
    source_reference: `candidate:${id}`,
  } as EvolutionLineage;
  backend.mockResolvedValueOnce({
    owner_account_id: id,
    description: "Source strategy",
  });
  expect((await withLineageOrigin(lineage))?.display_origin).toEqual({
    description: "Source strategy",
    href: `/lab/${id}`,
  });
  backend.mockResolvedValueOnce({
    owner_account_id: "another-owner",
    description: "PRIVATE",
  });
  expect((await withLineageOrigin(lineage))?.display_origin).toBeNull();
  backend.mockClear();
  expect(
    (
      await withLineageOrigin({
        ...lineage,
        source_reference: "candidate:invalid",
      })
    )?.display_origin,
  ).toBeNull();
  expect(backend).not.toHaveBeenCalled();
});

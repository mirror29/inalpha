import { beforeEach, describe, expect, it, vi } from "vitest";

const backendFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/backend", () => ({ backendFetch, BackendError: class extends Error {} }));
vi.mock("@/lib/evolution-capability", () => ({
  getEventEvolutionCapability: async () => ({ event_evolution_enabled: false }),
}));

import { GET } from "./route";
import { GET as list } from "../route";

const id = "11111111-1111-4111-8111-111111111111";

describe("existing evolution loop reads", () => {
  beforeEach(() => backendFetch.mockReset());

  it("lists existing workflows independently of the launch capability", async () => {
    backendFetch.mockResolvedValue({ items: [{ loop_id: id, status: "target_resolved" }] });
    const response = await list();
    expect(response.status).toBe(200);
    expect((await response.json()).loops).toEqual([{ loop_id: id, status: "target_resolved" }]);
  });

  it("keeps an owned workflow readable while new launches are disabled", async () => {
    backendFetch.mockResolvedValue({ loop_id: id, status: "baseline_ready", state_version: 2 });
    const response = await GET(new Request("http://localhost/api/evolution/loops/" + id), {
      params: Promise.resolve({ loopId: id }),
    });
    expect(response.status).toBe(200);
    expect((await response.json()).loop).toMatchObject({ status: "baseline_ready", state_version: 2 });
    expect(backendFetch).toHaveBeenCalledWith("evolver", `/api/v1/evolution-loops/${id}`, { timeoutMs: 5_000 });
  });
});

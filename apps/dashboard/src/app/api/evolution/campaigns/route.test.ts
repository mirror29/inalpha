import { beforeEach, describe, expect, it, vi } from "vitest";

const backendFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/backend", () => ({ backendFetch, BackendError: class extends Error {} }));
vi.mock("@/lib/evolution-capability", () => ({
  getEventEvolutionCapability: async () => ({ event_evolution_enabled: false, reason: "disabled" }),
}));

import { GET } from "./route";
import { GET as detail, POST as adopt } from "./[campaignId]/route";

const id = "11111111-1111-4111-8111-111111111111";
const context = { params: Promise.resolve({ campaignId: id }) };
const request = new Request(`http://localhost/api/evolution/campaigns/${id}`);

describe("campaign history with launches disabled", () => {
  beforeEach(() => backendFetch.mockReset());
  it("allows owned list and detail reads but still blocks adoption", async () => {
    backendFetch.mockResolvedValueOnce({ items: [{ campaign_id: id }] });
    const list = await GET();
    expect(list.status).toBe(200);
    expect((await list.json()).campaigns).toEqual([{ campaign_id: id }]);
    backendFetch.mockResolvedValueOnce({ campaign_id: id });
    expect((await detail(request, context)).status).toBe(200);
    backendFetch.mockClear();
    expect((await adopt(request, context)).status).toBe(503);
    expect(backendFetch).not.toHaveBeenCalled();
  });
});

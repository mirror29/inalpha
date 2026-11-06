import { beforeEach, describe, expect, it, vi } from "vitest";
const backendFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/backend", () => ({
  backendFetch,
  BackendError: class extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
}));
import { BackendError } from "@/lib/backend";
import { GET } from "./route";
const id = "11111111-1111-4111-8111-111111111111";
const context = { params: Promise.resolve({ campaignId: id }) };

/** Invalid references never reach the backend; owned evidence remains readable with launches disabled. */
describe("campaign evidence pagination", () => {
  beforeEach(() => {
    backendFetch.mockReset();
  });
  it("forwards pagination through owner-scoped backendFetch", async () => {
    const page = { items: [], limit: 24, offset: 24, has_more: false };
    backendFetch.mockResolvedValue(page);
    const response = await GET(
      new Request("http://localhost/api?offset=24"),
      context,
    );
    expect(await response.json()).toEqual(page);
    expect(backendFetch).toHaveBeenCalledWith(
      "evolver",
      `/api/v1/campaigns/${id}/implementations`,
      { query: { limit: 24, offset: 24 }, timeoutMs: 5000 },
    );
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });
  it.each([
    "?offset=-1",
    "?limit=NaN",
    "?limit=101",
    "?generation=0",
    "?generation=6",
  ])("rejects malformed pagination %s", async (query) => {
    expect(
      (await GET(new Request(`http://localhost/api${query}`), context)).status,
    ).toBe(400);
    expect(backendFetch).not.toHaveBeenCalled();
  });
  it("filters evidence to the selected generation", async () => {
    backendFetch.mockResolvedValue({ items: [], has_more: false });
    await GET(new Request("http://localhost/api?generation=3"), context);
    expect(backendFetch).toHaveBeenCalledWith(
      "evolver",
      `/api/v1/campaigns/${id}/implementations`,
      { query: { limit: 24, offset: 0, generation: 3 }, timeoutMs: 5000 },
    );
  });
  it("retains backend access-denied/not-found status", async () => {
    backendFetch.mockRejectedValue(new BackendError(404, "not found"));
    expect(
      (await GET(new Request("http://localhost/api"), context)).status,
    ).toBe(404);
  });
});

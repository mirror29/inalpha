import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendFetch } from "@/lib/backend";

import { GET, POST } from "./route";

vi.mock("@/lib/backend", () => ({ backendFetch: vi.fn() }));

const mockedBackendFetch = vi.mocked(backendFetch);

/** Calls the dynamic approval route with an isolated JSON request. */
async function callRoute(body: string) {
  return await POST(
    new NextRequest("http://dashboard.test/api/permissions/request-1/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
    { params: Promise.resolve({ id: "request-1" }) },
  );
}

beforeEach(() => { mockedBackendFetch.mockReset(); });

describe("approval response BFF", () => {
  it("forwards an explicit owner decision to the private Mastra API", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, decision: "allow" });

    const response = await callRoute(JSON.stringify({ decision: "allow" }));

    expect(response.status).toBe(200);
    expect(mockedBackendFetch).toHaveBeenCalledWith(
      "mastra",
      "/permissions/request-1/respond",
      expect.objectContaining({ method: "POST", body: { decision: "allow" } }),
    );
  });

  it("rejects malformed decisions before contacting Mastra", async () => {
    expect((await callRoute("not-json")).status).toBe(400);
    expect((await callRoute(JSON.stringify({ decision: "yes" }))).status).toBe(400);
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });
});


describe("historical approval availability", () => {
  const read = () => GET(new NextRequest("http://dashboard.test/api/permissions/request-1/respond"), {
    params: Promise.resolve({ id: "request-1" }),
  });
  it("returns only the live deadline without tool inputs", async () => {
    mockedBackendFetch.mockResolvedValue({ pending: [
      { requestId: "request-1", deadline: "2026-10-09T12:00:00Z", toolInput: "private" },
    ] });
    const response = await read();
    expect(await response.json()).toEqual({ status: "pending", deadline: "2026-10-09T12:00:00Z" });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });
  it("marks expired or absent requests unavailable without replaying them", async () => {
    mockedBackendFetch.mockResolvedValue({ pending: [] });
    expect(await (await read()).json()).toEqual({ status: "unavailable" });
    expect(mockedBackendFetch).toHaveBeenCalledWith("mastra", "/permissions/pending", { timeoutMs: 5_000 });
  });
  it("does not misreport backend failure as expiry", async () => {
    mockedBackendFetch.mockRejectedValue(new Error("private failure"));
    const response = await read();
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "approval_status_unavailable" });
  });
  it("preserves expired decision status for the UI", async () => {
    mockedBackendFetch.mockRejectedValue({ status: 404 });
    expect((await callRoute(JSON.stringify({ decision: "allow" }))).status).toBe(404);
  });
});

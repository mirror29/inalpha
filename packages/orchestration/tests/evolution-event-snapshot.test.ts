import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/auth.js", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/auth.js")>(),
  resolveRequestSubject: vi.fn(async () => "owned-account"),
  mintServiceToken: vi.fn(async () => "scoped-service-token"),
}));

import { clearSettings, setSettings } from "../src/config.js";
import { createAutomaticEventSnapshot } from "../src/tools/evolver.js";

const config = { venue: "binance", symbol: "SOL/USDT", as_of: "2026-10-09T06:25:54.528Z" };
const asset = { asset_id: "asset:SOL", event_asset_code: "SOL", venue: "binance", symbol: "SOL/USDT" };

beforeEach(() => {
  setSettings({ dataServiceUrl: "http://data.test" });
});

afterEach(() => {
  clearSettings();
  vi.unstubAllGlobals();
});

describe("automatic event evidence snapshot", () => {
  it.each(["regulatory", "upgrade", "unlock", "burn", "partnership", "macro", "other"])(
    "retains visible %s evidence without treating it as a direct trigger",
    async (eventType) => {
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("/assets/resolve")) return Response.json(asset);
        expect(url).toBe("http://data.test/events/snapshots");
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({
          cutoff: config.as_of, asset_ids: ["asset:SOL"], assets: ["SOL"],
          policy_version: "all-visible-facts-v1",
        });
        return Response.json({ snapshot_id: "frozen-evidence", fact_count: body.event_types.includes(eventType) ? 1 : 0 });
      }));
      await expect(createAutomaticEventSnapshot(config)).resolves.toMatchObject({ snapshotId: "frozen-evidence", asset });
    },
  );

  it("rejects genuinely empty evidence instead of declaring a workflow started", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(
      url.includes("/assets/resolve") ? asset : { snapshot_id: "empty", fact_count: 0 },
    )));
    await expect(createAutomaticEventSnapshot(config)).rejects.toThrow("EVENT_SNAPSHOT_EMPTY");
  });

  it("preserves an explicitly frozen snapshot without creating another one", async () => {
    const fetch = vi.fn(async (url: string) => {
      expect(url).toContain("/assets/resolve");
      return Response.json(asset);
    });
    vi.stubGlobal("fetch", fetch);
    await expect(createAutomaticEventSnapshot(config, undefined, "existing")).resolves.toMatchObject({ snapshotId: "existing" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

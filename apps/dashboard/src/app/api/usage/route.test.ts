import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ session: vi.fn(), query: vi.fn(), release: vi.fn(), connect: vi.fn() }));
vi.mock("@/lib/session", () => ({ readSession: mocks.session }));
vi.mock("@/lib/db", () => ({ getPool: () => ({ connect: mocks.connect }) }));
import { GET } from "./route";

beforeEach(() => {
  vi.resetAllMocks();
  mocks.connect.mockResolvedValue({ query: mocks.query, release: mocks.release });
  mocks.query.mockResolvedValue({ rows: [] });
});
it("does not query the ledger for unauthenticated users", async () => {
  mocks.session.mockResolvedValue(null);
  expect((await GET(new Request("http://test/api/usage"))).status).toBe(401);
  expect(mocks.connect).not.toHaveBeenCalled();
});
it("binds queries to session owner and exposes missing usage separately", async () => {
  mocks.session.mockResolvedValue({ subject: "alice" });
  mocks.query.mockImplementation(async (sql: string) => ({ rows: sql.includes("COUNT(*)::int calls") ? [{ calls: 1, unknown_usage_calls: 1, known_cost_usd: null }] : [] }));
  const response = await GET(new Request("http://test/api/usage?auth_sub=bob"));
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.summary.known_cost_usd).toBeNull();
  const selects = mocks.query.mock.calls.filter(([sql]) => sql.includes("FROM llm_usage_calls"));
  expect(selects).toHaveLength(2);
  for (const [, values] of selects) expect(values[0]).toBe("alice");
  expect(mocks.release).toHaveBeenCalled();
});

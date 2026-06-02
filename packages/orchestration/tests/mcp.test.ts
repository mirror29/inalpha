/**
 * MCP 子系统单测（ADR-0009）——config 加载 / JSON Schema→Zod / manager 韧性。
 *
 * 全部用注入的 fake client factory，不碰真实网络（真实连通性见 ``pnpm smoke:mcp``）。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  loadMcpConfigFromFile,
  McpConfigSchema,
} from "../src/mcp/config.js";
import { jsonSchemaToZod } from "../src/mcp/schema.js";
import {
  loadMcpTools,
  type McpClientFactory,
  type McpClientLike,
  type McpToolDescriptor,
} from "../src/mcp/manager.js";

afterEach(() => {
  vi.restoreAllMocks();
});

// ────────────────────────────────────────────────────────────────────
// config 加载
// ────────────────────────────────────────────────────────────────────

describe("loadMcpConfigFromFile", () => {
  it("文件不存在 → 空配置（不抛）", () => {
    const cfg = loadMcpConfigFromFile("/nonexistent/path/mcp.config.json");
    expect(cfg.mcpServers).toEqual({});
  });

  it("默认 disabled=false / type=http（schema default）", () => {
    const parsed = McpConfigSchema.parse({
      mcpServers: { foo: { url: "https://x.test/mcp" } },
    });
    expect(parsed.mcpServers.foo.type).toBe("http");
    expect(parsed.mcpServers.foo.disabled).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// JSON Schema → Zod
// ────────────────────────────────────────────────────────────────────

describe("jsonSchemaToZod", () => {
  it("object：required 必填 / 其余 optional / 容忍额外键", () => {
    const z = jsonSchemaToZod({
      type: "object",
      properties: {
        symbol: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["symbol"],
    });
    expect(z.safeParse({ symbol: "BTC" }).success).toBe(true);
    expect(z.safeParse({ symbol: "BTC", extra: 1 }).success).toBe(true); // passthrough
    expect(z.safeParse({ limit: 5 }).success).toBe(false); // 缺 required symbol
  });

  it("string enum → 限定取值", () => {
    const z = jsonSchemaToZod({
      type: "object",
      properties: { side: { enum: ["buy", "sell"] } },
      required: ["side"],
    });
    expect(z.safeParse({ side: "buy" }).success).toBe(true);
    expect(z.safeParse({ side: "hold" }).success).toBe(false);
  });

  it("缺失 / 非对象 schema → 接受任意参数（不抛）", () => {
    expect(jsonSchemaToZod(undefined).safeParse({ anything: 1 }).success).toBe(true);
    expect(jsonSchemaToZod(null).safeParse({}).success).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────
// manager 韧性
// ────────────────────────────────────────────────────────────────────

/** 造一个返回固定 tool 清单的 fake client。 */
function fakeClient(
  tools: McpToolDescriptor[],
  onCall?: (name: string, args: Record<string, unknown>) => unknown,
): McpClientLike {
  return {
    async connect() {},
    async listTools() {
      return { tools };
    },
    async callTool({ name, arguments: args }) {
      return onCall ? onCall(name, args) : { ok: true, name, args };
    },
    async close() {},
  };
}

describe("loadMcpTools", () => {
  it("happy path：tool 被包成 mcp__<server>__<verb>", async () => {
    const factory: McpClientFactory = () =>
      fakeClient([
        { name: "get_price", inputSchema: { type: "object", properties: {} } },
        { name: "get_global" },
      ]);
    const tools = await loadMcpTools({
      config: { mcpServers: { coingecko: { type: "http", url: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
    });
    expect(tools.map((t) => t.id).sort()).toEqual([
      "mcp__coingecko__get_global",
      "mcp__coingecko__get_price",
    ]);
  });

  it("disabled server 被跳过", async () => {
    const factory: McpClientFactory = () => fakeClient([{ name: "x" }]);
    const tools = await loadMcpTools({
      config: { mcpServers: { paid: { type: "http", url: "x", disabled: true } } },
      clientFactory: factory,
      env: {},
    });
    expect(tools).toHaveLength(0);
  });

  it("requiredEnv 缺失 → 跳过该 server（不抛）", async () => {
    const factory: McpClientFactory = () => fakeClient([{ name: "x" }]);
    const tools = await loadMcpTools({
      config: {
        mcpServers: {
          factset: { type: "http", url: "x", disabled: false, requiredEnv: ["FACTSET_API_KEY"] },
        },
      },
      clientFactory: factory,
      env: {}, // 没有 FACTSET_API_KEY
    });
    expect(tools).toHaveLength(0);
  });

  it("requiredEnv 齐全 → 该 server 正常加载", async () => {
    const factory: McpClientFactory = () => fakeClient([{ name: "x" }]);
    const tools = await loadMcpTools({
      config: {
        mcpServers: {
          factset: { type: "http", url: "x", disabled: false, requiredEnv: ["FACTSET_API_KEY"] },
        },
      },
      clientFactory: factory,
      env: { FACTSET_API_KEY: "sk-real" },
    });
    expect(tools.map((t) => t.id)).toEqual(["mcp__factset__x"]);
  });

  it("单 server 连接失败 → 其余照常加载，不抛", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const factory: McpClientFactory = (name) => {
      if (name === "broken") {
        return {
          async connect() {},
          async listTools() {
            throw new Error("ECONNREFUSED");
          },
          async callTool() {
            return null;
          },
          async close() {},
        };
      }
      return fakeClient([{ name: "ok_tool" }]);
    };
    const tools = await loadMcpTools({
      config: {
        mcpServers: {
          broken: { type: "http", url: "x", disabled: false },
          good: { type: "http", url: "y", disabled: false },
        },
      },
      clientFactory: factory,
      env: {},
    });
    expect(tools.map((t) => t.id)).toEqual(["mcp__good__ok_tool"]);
  });

  it("execute 把 input 透传给 client.callTool", async () => {
    const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
    const factory: McpClientFactory = () =>
      fakeClient([{ name: "get_price" }], (name, args) => {
        calls.push({ name, args });
        return { price: 42 };
      });
    const tools = await loadMcpTools({
      config: { mcpServers: { coingecko: { type: "http", url: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
    });
    const result = await tools[0].execute?.({ id: "bitcoin" });
    expect(calls).toEqual([{ name: "get_price", args: { id: "bitcoin" } }]);
    expect(result).toEqual({ price: 42 });
  });
});

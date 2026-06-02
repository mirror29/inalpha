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
  closeAllMcpClients,
  loadMcpTools,
  resetMcpCleanupHooks,
  type McpClientFactory,
  type McpClientLike,
  type McpToolDescriptor,
} from "../src/mcp/manager.js";

afterEach(async () => {
  vi.restoreAllMocks();
  // 清空模块级 _liveClients + 移除进程清理监听，避免跨用例污染
  await closeAllMcpClients();
  resetMcpCleanupHooks();
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

  it("nullable：type:['string','null'] → 接受 null（LLM 可传 null）", () => {
    const z = jsonSchemaToZod({
      type: "object",
      properties: { note: { type: ["string", "null"] } },
      required: ["note"],
    });
    expect(z.safeParse({ note: "x" }).success).toBe(true);
    expect(z.safeParse({ note: null }).success).toBe(true);
    expect(z.safeParse({ note: 1 }).success).toBe(false);
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

  it("单 server 连接失败 → 其余照常加载，不抛，且失败 client 被 close（不泄漏子进程）", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    let brokenClosed = false;
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
          async close() {
            brokenClosed = true;
          },
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
    // listTools 抛错后，失败 client 被显式 close（释放可能已 fork 的 stdio 子进程）
    expect(brokenClosed).toBe(true);
  });

  it("closeAllMcpClients 关闭所有已连接 client（释放 stdio 子进程）", async () => {
    const closed: string[] = [];
    const makeClient = (tag: string): McpClientLike => ({
      async connect() {},
      async listTools() {
        return { tools: [{ name: "t" }] };
      },
      async callTool() {
        return null;
      },
      async close() {
        closed.push(tag);
      },
    });
    const factory: McpClientFactory = (name) => makeClient(name);
    await loadMcpTools({
      config: {
        mcpServers: {
          a: { type: "http", url: "x", disabled: false },
          b: { type: "http", url: "y", disabled: false },
        },
      },
      clientFactory: factory,
      env: {},
    });
    await closeAllMcpClients();
    expect(closed.sort()).toEqual(["a", "b"]);
    // 幂等：再调一次不重复 close
    await closeAllMcpClients();
    expect(closed.sort()).toEqual(["a", "b"]);
  });

  it("listTools 挂起 → 超时快速跳过该 server，不阻塞，且 close 释放连接", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    let closed = false;
    const factory: McpClientFactory = () => ({
      async connect() {},
      listTools(): Promise<{ tools: McpToolDescriptor[] }> {
        return new Promise(() => {}); // 永不 resolve（模拟端点不可达）
      },
      async callTool() {
        return null;
      },
      async close() {
        closed = true;
      },
    });
    const tools = await loadMcpTools({
      config: { mcpServers: { slow: { type: "http", url: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
      listToolsTimeoutMs: 20,
    });
    expect(tools).toEqual([]);
    expect(closed).toBe(true); // 超时后该 client 被 close
  });

  it("stdio server 挂进程清理监听，resetMcpCleanupHooks 能复位（热重载不孤儿）", async () => {
    const beforeExit0 = process.listenerCount("beforeExit");
    const sigint0 = process.listenerCount("SIGINT");
    const factory: McpClientFactory = () => fakeClient([{ name: "t" }]);
    await loadMcpTools({
      config: { mcpServers: { local: { type: "stdio", command: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
    });
    // stdio → beforeExit + SIGINT + SIGTERM 监听都挂上
    expect(process.listenerCount("beforeExit")).toBe(beforeExit0 + 1);
    expect(process.listenerCount("SIGINT")).toBe(sigint0 + 1);

    resetMcpCleanupHooks();
    // 复位后监听数回到基线（热重载下次连接才能重新挂）
    expect(process.listenerCount("beforeExit")).toBe(beforeExit0);
    expect(process.listenerCount("SIGINT")).toBe(sigint0);
  });

  it("execute 透传 input + 解包 CallToolResult 的 text", async () => {
    const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
    const factory: McpClientFactory = () =>
      fakeClient([{ name: "get_price" }], (name, args) => {
        calls.push({ name, args });
        return { content: [{ type: "text", text: "BTC = 42" }] };
      });
    const tools = await loadMcpTools({
      config: { mcpServers: { coingecko: { type: "http", url: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
    });
    const result = await tools[0].execute?.({ id: "bitcoin" });
    expect(calls).toEqual([{ name: "get_price", args: { id: "bitcoin" } }]);
    // 解包成纯文本（不是嵌套 content 数组）
    expect(result).toBe("BTC = 42");
  });

  it("execute 在 isError:true 时 throw（不让 Mastra 当成功）", async () => {
    const factory: McpClientFactory = () =>
      fakeClient([{ name: "boom" }], () => ({
        isError: true,
        content: [{ type: "text", text: "rate limit exceeded" }],
      }));
    const tools = await loadMcpTools({
      config: { mcpServers: { coingecko: { type: "http", url: "x", disabled: false } } },
      clientFactory: factory,
      env: {},
    });
    await expect(tools[0].execute?.({})).rejects.toThrow("rate limit exceeded");
  });
});

/**
 * MCP client 管理层（ADR-0009 产品化）。
 *
 * 职责：读 ``loadMcpConfig()`` 的 server 清单 → 逐个连接（http / stdio）→ ``listTools()``
 * → 把每个 MCP tool 包成 Mastra ``createTool``，命名 ``mcp__<server>__<verb>``（ADR-0009
 * §约定 4）。包出来的 tool 由调用方（``wired-tools.ts``）过 ``wireToolList`` 套上同一套
 * hooks + permissions，无需新权限代码。
 *
 * **韧性（ADR-0009 §约定 2/3）**：
 * - ``disabled: true`` 的 server 跳过
 * - ``requiredEnv`` 缺失的 server 跳过 + 告警（免费用户不会因没填付费 key 而启动失败）
 * - 单个 server 连接 / listTools 失败 → 告警 + 跳过，**不抛**、不阻塞其余 server 与 Mastra 启动
 *
 * transport 直接用 ``@modelcontextprotocol/sdk``（ADR-0009 §transport：起步 stdio + http），
 * 不依赖 Mastra 封装。client factory 可注入，便于单测（mock 掉真实网络）。
 *
 * @module mcp/manager
 */
import { createTool } from "@mastra/core/tools";

import { loadMcpConfig, type McpConfig, type McpServerConfig } from "./config.js";
import { jsonSchemaToZod } from "./schema.js";

/** MCP tool 描述（``listTools()`` 返回项的最小形态）。 */
export interface McpToolDescriptor {
  name: string;
  description?: string;
  inputSchema?: unknown;
}

/** MCP client 的最小接口（便于单测注入 fake）。 */
export interface McpClientLike {
  connect(): Promise<void>;
  listTools(): Promise<{ tools: McpToolDescriptor[] }>;
  callTool(args: { name: string; arguments: Record<string, unknown> }): Promise<unknown>;
  close(): Promise<void>;
}

/** 按 server 配置创建一个（未连接的）client 的工厂。 */
export type McpClientFactory = (
  name: string,
  server: McpServerConfig,
) => McpClientLike;

/** wireToolList 之前的原始 Mastra tool（id + execute）。 */
export interface RawMcpTool {
  id: string;
  description?: string;
  execute?: (input: unknown, ctx?: unknown) => Promise<unknown> | unknown;
  [key: string]: unknown;
}

export interface LoadMcpToolsOptions {
  /** 自定义配置（缺省走 ``loadMcpConfig()``）。 */
  config?: McpConfig;
  /** 自定义 client 工厂（缺省走 ``@modelcontextprotocol/sdk``）；单测注入 fake。 */
  clientFactory?: McpClientFactory;
  /** env 来源（缺省 ``process.env``）；单测可注入。 */
  env?: Record<string, string | undefined>;
  /**
   * 单个 server ``listTools()`` 超时（毫秒），缺省 ``DEFAULT_LIST_TOOLS_TIMEOUT_MS``（10s）。
   *
   * **关键**：orchestrator 的 dynamic tools 首次触发即连接 enabled server；若端点不可达，
   * 无超时会挂到 OS TCP 超时（Linux ≈2min），期间所有 orchestrator 请求阻塞在 memoized
   * Promise 上。超时后 try/catch 吞掉该 server、继续其余（永不阻塞主链路）。
   */
  listToolsTimeoutMs?: number;
}

/** 单 server listTools 默认超时（毫秒）。 */
export const DEFAULT_LIST_TOOLS_TIMEOUT_MS = 10_000;

/** 把 header 值里的 ``${VAR}`` 占位替换成 env 值（缺失留空串）。 */
function resolveEnvPlaceholders(
  headers: Record<string, string> | undefined,
  env: Record<string, string | undefined>,
): Record<string, string> | undefined {
  if (!headers) return undefined;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) {
    out[k] = v.replace(/\$\{(\w+)\}/g, (_, name: string) => env[name] ?? "");
  }
  return out;
}

/**
 * 默认 client 工厂——用 ``@modelcontextprotocol/sdk`` 按 transport 类型建 client。
 *
 * 动态 import SDK：让没装 / 用不到 MCP 的场景不为这条路径付加载成本，也让单测
 * 走注入的 fake factory 时完全不碰真实 SDK。
 */
function buildDefaultClient(
  name: string,
  server: McpServerConfig,
  env: Record<string, string | undefined>,
): McpClientLike {
  let connected: McpClientLike | null = null;

  async function ensure(): Promise<McpClientLike> {
    if (connected) return connected;
    const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
    const client = new Client({ name: `inalpha-${name}`, version: "0.1.0" });

    if (server.type === "stdio") {
      if (!server.command) {
        throw new Error(`MCP server '${name}' type=stdio 缺少 command`);
      }
      const { StdioClientTransport } = await import(
        "@modelcontextprotocol/sdk/client/stdio.js"
      );
      const transport = new StdioClientTransport({
        command: server.command,
        args: server.args ?? [],
      });
      await client.connect(transport);
    } else {
      if (!server.url) {
        throw new Error(`MCP server '${name}' type=http 缺少 url`);
      }
      const { StreamableHTTPClientTransport } = await import(
        "@modelcontextprotocol/sdk/client/streamableHttp.js"
      );
      const headers = resolveEnvPlaceholders(server.headers, env);
      const transport = new StreamableHTTPClientTransport(new URL(server.url), {
        requestInit: headers ? { headers } : undefined,
      });
      await client.connect(transport);
    }
    connected = client as unknown as McpClientLike;
    return connected;
  }

  // 包一层：connect 在第一次用到时真正建立（listTools 触发）
  return {
    async connect() {
      await ensure();
    },
    async listTools() {
      const c = await ensure();
      return c.listTools();
    },
    async callTool(args) {
      const c = await ensure();
      return c.callTool(args);
    },
    async close() {
      if (connected) await connected.close();
      connected = null;
    },
  };
}

// ────────────────────────────────────────────────────────────────────
// 子进程清理（ADR-0009）：stdio transport fork 子进程，需在退出时 close 释放，
// 否则 Mastra 重启会孤儿化累积。
// ────────────────────────────────────────────────────────────────────

/** 已连接的 MCP client 注册表，供退出清理。 */
const _liveClients = new Set<McpClientLike>();
/** 已注册的清理监听引用——保留以便热重载 / 测试时 removeListener 复位（见 resetMcpCleanupHooks）。 */
let _beforeExitHandler: (() => void) | null = null;
const _signalHandlers: Array<[NodeJS.Signals, () => void]> = [];

/** 信号 → 约定退出码（128 + signo）。覆盖成 0 会让 Docker/K8s on-failure restart 失效。 */
const _SIGNAL_EXIT_CODES: Record<string, number> = { SIGINT: 130, SIGTERM: 143 };

/**
 * 关闭所有已建立的 MCP client（释放 stdio 子进程）。
 *
 * 显式 API：Mastra 关闭回调 / 测试 / 热重载可主动调；进程退出钩子也会调。
 * 幂等：close 失败被吞（best-effort）。
 *
 * @returns 全部 close 完成（或失败被吞）后 resolve
 */
export async function closeAllMcpClients(): Promise<void> {
  const clients = [..._liveClients];
  _liveClients.clear();
  await Promise.allSettled(clients.map((c) => c.close()));
}

/**
 * 首次连接时挂一次进程退出清理。
 *
 * - ``beforeExit``（async 安全，正常事件循环排空时）：所有 transport 都挂，最尽力优雅关闭。
 * - ``SIGINT`` / ``SIGTERM``：**仅在出现 stdio client 时**才挂——避免无谓干扰 host 的信号
 *   处理；HTTP-only（默认 CoinGecko）不碰信号。信号到达时关闭子进程后再按默认码退出。
 *
 * @param hasStdio - 本次连接的 server 是否为 stdio transport
 */
function hookProcessCleanupOnce(hasStdio: boolean): void {
  if (!_beforeExitHandler) {
    _beforeExitHandler = () => {
      void closeAllMcpClients();
    };
    process.once("beforeExit", _beforeExitHandler);
  }
  if (hasStdio && _signalHandlers.length === 0) {
    for (const sig of ["SIGINT", "SIGTERM"] as const) {
      const handler = () => {
        // 关子进程后按约定码退出（SIGINT→130 / SIGTERM→143），不要用 0——
        // 否则 Docker/K8s on-failure restart policy 会把信号 kill 误判成正常结束。
        void closeAllMcpClients().finally(() => process.exit(_SIGNAL_EXIT_CODES[sig] ?? 0));
      };
      _signalHandlers.push([sig, handler]);
      process.once(sig, handler);
    }
  }
}

/**
 * 移除已注册的进程清理监听并复位（热重载 / 测试用）。
 *
 * ``_beforeExitHandler`` / ``_signalHandlers`` 是模块级状态，``process.once`` 注册后
 * 不会自复位。热重载路径若不复位，新一轮的 stdio client 因标志仍"已挂"而不再注册
 * 清理钩子。**调用前通常应先 ``closeAllMcpClients()``** 关掉存量 client。
 */
export function resetMcpCleanupHooks(): void {
  if (_beforeExitHandler) {
    process.removeListener("beforeExit", _beforeExitHandler);
    _beforeExitHandler = null;
  }
  for (const [sig, handler] of _signalHandlers) {
    process.removeListener(sig, handler);
  }
  _signalHandlers.length = 0;
}

/** 给 promise 套超时；超时 reject（不取消原 promise，但调用方据此快速 fail）。 */
async function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} 超时（>${ms}ms）`)), ms);
  });
  try {
    return await Promise.race([p, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 加载所有可用 MCP server 的 tool，包成 Mastra raw tool 数组。
 *
 * 永不抛错：任何 server 失败都被吞掉并告警，返回已成功加载的 tool（可能为空）。
 *
 * @param opts - 配置 / client 工厂 / env 注入（均可选，缺省走真实来源）
 * @returns 命名为 ``mcp__<server>__<verb>`` 的 raw tool 数组
 */
export async function loadMcpTools(
  opts: LoadMcpToolsOptions = {},
): Promise<RawMcpTool[]> {
  const config = opts.config ?? loadMcpConfig();
  const env = opts.env ?? process.env;
  const factory =
    opts.clientFactory ?? ((name, server) => buildDefaultClient(name, server, env));
  const listToolsTimeoutMs = opts.listToolsTimeoutMs ?? DEFAULT_LIST_TOOLS_TIMEOUT_MS;

  const tools: RawMcpTool[] = [];

  for (const [name, server] of Object.entries(config.mcpServers)) {
    if (server.disabled) {
      console.info(`[mcp] server '${name}' disabled，跳过`);
      continue;
    }
    const missing = (server.requiredEnv ?? []).filter((k) => !env[k]?.trim());
    if (missing.length > 0) {
      console.warn(
        `[mcp] server '${name}' 缺少 env [${missing.join(", ")}]，跳过` +
          `（配齐后即可启用，主链路不受影响）`,
      );
      continue;
    }

    let client: McpClientLike | undefined;
    try {
      client = factory(name, server);
      // **先登记再 listTools**：stdio transport 在 factory→listTools 之间即可能 fork
      // 子进程，若 listTools 抛错而此前未登记，子进程就孤儿化（ADR-0009 §约定）。
      // HTTP 无子进程，登记只为统一 closeAllMcpClients 语义；仅 stdio 才挂信号清理。
      _liveClients.add(client);
      hookProcessCleanupOnce(server.type === "stdio");
      // 超时保护：端点不可达时快速 fail（默认 10s），不挂到 OS TCP 超时阻塞 orchestrator
      const { tools: mcpTools } = await withTimeout(
        client.listTools(),
        listToolsTimeoutMs,
        `mcp '${name}' listTools`,
      );
      for (const t of mcpTools) {
        tools.push(wrapMcpTool(name, t, client));
      }
      console.info(`[mcp] server '${name}' 加载 ${mcpTools.length} 个 tool`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 失败的 server：立刻关掉可能已 fork 的子进程并移出注册表，别留孤儿
      if (client) {
        await client.close().catch(() => {});
        _liveClients.delete(client);
      }
      console.warn(`[mcp] server '${name}' 连接 / listTools 失败：${msg}；跳过该 server`);
    }
  }

  return tools;
}

/** 把单个 MCP tool 描述包成 Mastra createTool（id 加 ``mcp__<server>__`` 前缀）。 */
function wrapMcpTool(
  serverName: string,
  descriptor: McpToolDescriptor,
  client: McpClientLike,
): RawMcpTool {
  const id = `mcp__${serverName}__${descriptor.name}`;
  // description 是喂给 LLM 的 tool schema，统一英文（与 MCP server 原 description 同语言）
  const description =
    (descriptor.description ?? `MCP tool ${descriptor.name} (server: ${serverName})`) +
    `\n\n[Source: MCP server '${serverName}', governed by Inalpha hooks + permissions]`;

  const tool = createTool({
    id,
    description,
    inputSchema: jsonSchemaToZod(descriptor.inputSchema),
    execute: async (inputData: unknown) => {
      const args =
        inputData && typeof inputData === "object"
          ? (inputData as Record<string, unknown>)
          : {};
      return client.callTool({ name: descriptor.name, arguments: args });
    },
  });

  return tool as unknown as RawMcpTool;
}

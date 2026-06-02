/**
 * MCP server 配置加载层（ADR-0009）。
 *
 * **schema 与 anthropics/financial-services 的 ``.mcp.json`` 同构**——
 * ``{ mcpServers: { <name>: { type, url, ... } } }``。这样持有 FactSet / Morningstar
 * 等订阅的用户可把官方 ``.mcp.json`` 直接丢进来即用，是"兼容 Anthropic 金融 MCP
 * 连接器目录"这个卖点的具体载体。
 *
 * **免费优先（ADR-0009 §2026-06-01 补充）**：默认 ``config/mcp.config.json`` 只启用
 * 零密钥公开端点（CoinGecko）；付费连接器以 ``disabled: true`` 形式作模板存在。整条
 * 主链路在不配置任何 key、不付费的前提下必须能跑通。
 *
 * 优先级：``INALPHA_MCP_CONFIG_FILE`` env → 包内 ``config/mcp.config.json`` → 空配置。
 *
 * 与 permissions 的 fail-fast 不同：**MCP 是可选增强**，配置缺失 / 不合法都
 * **不阻塞启动**（log 后返回空配置），与 ADR-0009 §约定 3"MCP 失败不阻塞 Mastra 启动"
 * 一致。路径用 ``import.meta.url`` 解析，不依赖 cwd。
 *
 * @module mcp/config
 */
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { z } from "zod";

/** 单个 MCP server 条目（financial-services .mcp.json 同构 + Inalpha 扩展字段）。 */
export const McpServerSchema = z.object({
  /** transport 类型；起步只支持 http / stdio（ADR-0009 §transport）。缺省 http。 */
  type: z.enum(["http", "stdio"]).default("http"),
  /** http transport 的端点 URL。 */
  url: z.string().optional(),
  /** stdio transport 的可执行命令。 */
  command: z.string().optional(),
  /** stdio transport 的命令参数。 */
  args: z.array(z.string()).optional(),
  /** http transport 的额外 header（如 Authorization）。值支持 ``${ENV}`` 占位。 */
  headers: z.record(z.string(), z.string()).optional(),
  /** Inalpha 扩展：true 时跳过该 server（付费连接器默认 disabled 作模板）。 */
  disabled: z.boolean().default(false),
  /** Inalpha 扩展：所需 env 变量名；缺失则跳过该 server 并告警（免费用户不会因缺 key 而启动失败）。 */
  requiredEnv: z.array(z.string()).optional(),
  /** Inalpha 扩展：人类可读说明（仅文档用途）。 */
  description: z.string().optional(),
});

export type McpServerConfig = z.infer<typeof McpServerSchema>;

/** ``.mcp.json`` 顶层 schema。 */
export const McpConfigSchema = z.object({
  mcpServers: z.record(z.string(), McpServerSchema).default({}),
});

export type McpConfig = z.infer<typeof McpConfigSchema>;

const EMPTY_CONFIG: McpConfig = { mcpServers: {} };

/** 返回包内 ``config/mcp.config.json`` 的绝对路径。 */
export function resolveDefaultMcpConfigPath(): string {
  // this file: packages/orchestration/src/mcp/config.ts
  // target:    packages/orchestration/config/mcp.config.json
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "config", "mcp.config.json");
}

/**
 * 从给定绝对路径加载 + 校验 ``.mcp.json``。
 *
 * 与 permissions 不同：失败时**不抛**——log 后返回空配置（MCP 是可选增强，
 * 不该因配置问题阻塞整个 orchestration 启动）。
 *
 * @param absPath - 配置文件绝对路径
 * @returns 解析后的配置；文件缺失 / JSON 不合法 / schema 不匹配时返回空配置
 */
export function loadMcpConfigFromFile(absPath: string): McpConfig {
  if (!existsSync(absPath)) {
    return EMPTY_CONFIG;
  }
  let raw: string;
  try {
    raw = readFileSync(absPath, "utf8");
  } catch (err) {
    console.warn(`[mcp] failed to read config ${absPath}: ${String(err)}; 跳过 MCP`);
    return EMPTY_CONFIG;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    console.warn(`[mcp] invalid JSON in ${absPath}: ${String(err)}; 跳过 MCP`);
    return EMPTY_CONFIG;
  }
  const result = McpConfigSchema.safeParse(parsed);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join(".") || "<root>"}: ${i.message}`)
      .join("\n");
    console.warn(`[mcp] config schema mismatch in ${absPath}:\n${issues}\n跳过 MCP`);
    return EMPTY_CONFIG;
  }
  return result.data;
}

/**
 * 主入口：按优先级解析当前进程应使用的 MCP 配置。
 *
 * - ``INALPHA_MCP_CONFIG_FILE`` 设了 → 加载该文件
 * - 否则 → 加载包内默认 ``config/mcp.config.json``
 * - 都没有 → 空配置（不启用任何 MCP server）
 *
 * @returns 解析后的 MCP 配置（永不抛错）
 */
export function loadMcpConfig(): McpConfig {
  const envPath = process.env.INALPHA_MCP_CONFIG_FILE?.trim();
  if (envPath) {
    return loadMcpConfigFromFile(resolve(envPath));
  }
  return loadMcpConfigFromFile(resolveDefaultMcpConfigPath());
}

/**
 * MCP 子系统入口（ADR-0009）。
 *
 * 对外只暴露两类东西：
 * - 配置 / 管理层类型与函数（``loadMcpConfig`` / ``loadMcpTools`` / schema 转换）
 * - ``getMcpToolsCached()``：进程内 memoize 的 MCP tool 加载——orchestrator 的 dynamic
 *   tools 函数每次 invoke 都会调它，memoize 保证只真正连一次。
 *
 * @module mcp
 */
import { loadMcpTools, type LoadMcpToolsOptions, type RawMcpTool } from "./manager.js";

export {
  loadMcpConfig,
  loadMcpConfigFromFile,
  resolveDefaultMcpConfigPath,
  McpConfigSchema,
  McpServerSchema,
} from "./config.js";
export type { McpConfig, McpServerConfig } from "./config.js";
export { jsonSchemaToZod } from "./schema.js";
export { loadMcpTools } from "./manager.js";
export type {
  McpClientLike,
  McpClientFactory,
  McpToolDescriptor,
  RawMcpTool,
  LoadMcpToolsOptions,
} from "./manager.js";

let _cache: Promise<RawMcpTool[]> | null = null;

/**
 * Memoize 版 MCP tool 加载——首次调用真正连接，之后复用同一 Promise。
 *
 * 失败语义继承 ``loadMcpTools``（永不抛，最坏返回空数组），所以即便 MCP 全挂，
 * orchestrator 仍正常工作（只是少了 ``mcp__*`` tool）。
 *
 * @param opts - 透传给 ``loadMcpTools``；**仅首次调用生效**（memoize）
 * @returns 已加载的 raw MCP tool 数组
 */
export function getMcpToolsCached(opts?: LoadMcpToolsOptions): Promise<RawMcpTool[]> {
  if (!_cache) {
    _cache = loadMcpTools(opts).catch((err) => {
      // loadMcpTools 内部已吞错；这里再兜一层，确保 memoize 不缓存 rejected promise
      console.warn(`[mcp] getMcpToolsCached 兜底捕获：${String(err)}`);
      return [];
    });
  }
  return _cache;
}

/** 清空 memoize（测试 / 热重载用）。 */
export function resetMcpToolsCache(): void {
  _cache = null;
}

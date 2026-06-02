/**
 * MCP manager 端到端连通性 smoke（ADR-0009 产品化验证）。
 *
 * 跟 ``smoke-coingecko-mcp.ts``（裸 SDK spike）的区别：这个走 **Inalpha 的 manager**——
 * 读 ``config/mcp.config.json`` → ``loadMcpTools()`` → 验证 tool 被包成 ``mcp__<server>__*``。
 *
 * 用法：
 *
 *   pnpm smoke:mcp
 *
 * 期望：看到 ``mcp__coingecko__*`` 一批 tool；付费连接器（disabled / 缺 env）被跳过。
 * 不进 CI（要真实网络）。
 */
import { loadMcpConfig } from "../src/mcp/config.js";
import { loadMcpTools } from "../src/mcp/manager.js";

function divider(title: string): void {
  console.log("\n" + "─".repeat(64));
  console.log("  " + title);
  console.log("─".repeat(64));
}

async function main(): Promise<void> {
  divider("Case 1 · 读 config/mcp.config.json");
  const config = loadMcpConfig();
  const names = Object.keys(config.mcpServers);
  console.log(`✓ 配置内 ${names.length} 个 server：${names.join(", ")}`);
  const enabled = names.filter((n) => !config.mcpServers[n].disabled);
  console.log(`  其中 enabled：${enabled.join(", ") || "(无)"}`);

  divider("Case 2 · loadMcpTools()（真实连接 enabled server）");
  const tools = await loadMcpTools({ config });
  console.log(`✓ 加载 ${tools.length} 个 MCP tool`);
  for (const t of tools.slice(0, 12)) {
    console.log(`  - ${t.id}`);
  }
  if (tools.length > 12) console.log(`  ... 还有 ${tools.length - 12} 个`);

  divider("Case 3 · 校验命名前缀 mcp__<server>__");
  const bad = tools.filter((t) => !/^mcp__[a-z0-9-]+__/.test(t.id));
  if (bad.length > 0) {
    console.error(`✗ ${bad.length} 个 tool 命名不合规：${bad.map((t) => t.id).join(", ")}`);
    process.exit(1);
  }
  console.log("✓ 全部 tool 命名合规");

  divider("✅ MCP manager smoke 通过");
}

main().catch((err) => {
  console.error("smoke-mcp 失败:", err);
  process.exit(1);
});

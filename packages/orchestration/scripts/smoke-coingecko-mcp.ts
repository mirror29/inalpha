/**
 * CoinGecko 官方 MCP 连通性 spike —— Phase D 第一步。
 *
 * 验证：
 * 1. @modelcontextprotocol/sdk 能连 https://mcp.api.coingecko.com/mcp（公开端点，无 key）
 * 2. listTools 拿到 tool 清单
 * 3. 跑一个简单 tool（如 ping / get global / get top coins）拿真数据
 *
 * 用法：
 *
 *   pnpm smoke:coingecko
 *
 * 这一步通过后，下一步把感兴趣的 tool（onchain DEX 数据 / GeckoTerminal）
 * wrap 成 Inalpha 的 data.crypto.* tool 暴露给 orchestrator。
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const COINGECKO_MCP_URL = "https://mcp.api.coingecko.com/mcp";

function divider(title: string): void {
  console.log("\n" + "─".repeat(64));
  console.log("  " + title);
  console.log("─".repeat(64));
}

async function main(): Promise<void> {
  divider("Case 1 · 连接 CoinGecko 公开 MCP 端点");
  console.log(`URL: ${COINGECKO_MCP_URL}`);

  const transport = new StreamableHTTPClientTransport(new URL(COINGECKO_MCP_URL));
  const client = new Client({
    name: "inalpha-mcp-spike",
    version: "0.1.0",
  });

  await client.connect(transport);
  console.log("✓ connected");

  divider("Case 2 · listTools");
  const toolsResult = await client.listTools();
  console.log(`✓ 拿到 ${toolsResult.tools.length} 个 tool`);
  for (const tool of toolsResult.tools.slice(0, 10)) {
    console.log(`  - ${tool.name}${tool.description ? ` — ${tool.description.slice(0, 70)}` : ""}`);
  }
  if (toolsResult.tools.length > 10) {
    console.log(`  ... 还有 ${toolsResult.tools.length - 10} 个`);
  }

  divider("Case 3 · 找一个安全的只读 tool 调一下（验证返回 schema）");
  // 找带 "ping" / "global" / "trending" / "top" 关键字的 tool（应该是只读）
  const candidate = toolsResult.tools.find((t) =>
    /ping|global|trending|top|categor/i.test(t.name),
  );
  if (!candidate) {
    console.log("⚠ 没找到 ping/global/trending/top/category 类 tool，skip case 3");
  } else {
    console.log(`选中：${candidate.name}`);
    console.log(`inputSchema：${JSON.stringify(candidate.inputSchema).slice(0, 200)}`);
    const result = await client.callTool({
      name: candidate.name,
      arguments: {},
    });
    const preview = JSON.stringify(result).slice(0, 400);
    console.log(`✓ 调用成功，返回 preview:\n  ${preview}${preview.length >= 400 ? "..." : ""}`);
  }

  divider("Case 4 · 找 onchain / DEX 相关 tool 看看");
  const onchainTools = toolsResult.tools.filter((t) =>
    /onchain|dex|pool|gecko_terminal|gt_/i.test(t.name + (t.description ?? "")),
  );
  console.log(`匹配到 ${onchainTools.length} 个 onchain/DEX tool：`);
  for (const t of onchainTools.slice(0, 15)) {
    console.log(`  - ${t.name}`);
  }

  await client.close();
  divider("✅ CoinGecko MCP 连通性 spike 通过");
}

main().catch((err) => {
  console.error("smoke-coingecko 失败:", err);
  process.exit(1);
});

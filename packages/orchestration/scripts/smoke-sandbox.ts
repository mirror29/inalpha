/**
 * Sandbox spike 端到端 smoke —— Task #5 验收点。
 *
 * 不依赖任何 service / LLM：直接用 wiredOrchestratorTools 里那个 sandbox.run_code，
 * 走完整链路 audit-log hook → permission engine → LocalSubprocessProvider → 真跑。
 *
 * 用法：
 *
 *   pnpm smoke:sandbox
 *
 * 验收点：
 * 1. python case 1：sum(range(100)) → 4950
 * 2. node case：算斐波那契前 10 项
 * 3. 超时被 SIGKILL：timedOut=true
 * 4. permission ask 路径：timeoutMs=120000 被 ask 挡住
 * 5. audit-log 通过 PostToolUse hook 输出（标准输出可见）
 */
import { wireToolList } from "../src/mastra/wired-tools.js";
import { sandboxRunCodeTool } from "../src/tools/index.js";

type SandboxToolResult = {
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  timedOut?: boolean;
  truncated?: boolean;
  durationMs?: number;
  provider?: string;
  isError?: boolean;
  deniedBy?: string;
  hookMessage?: string;
};

function divider(title: string): void {
  console.log("\n" + "─".repeat(64));
  console.log("  " + title);
  console.log("─".repeat(64));
}

async function main(): Promise<void> {
  // 同 orchestrator agent 的注入方式 —— 拿到 hook+permission 包装后的 tool
  const [wrapped] = wireToolList([sandboxRunCodeTool]);
  if (!wrapped?.execute) {
    throw new Error("sandbox.run_code tool not wired");
  }

  // ────────────────────────────────────────────────────────────────────
  // Case 1：python sum(range(100))
  // ────────────────────────────────────────────────────────────────────
  divider("Case 1 · python sum(range(100)) 期望 4950");
  const r1 = (await wrapped.execute(
    {
      code: "print(sum(range(100)))",
      language: "python",
      timeoutMs: 5_000,
    },
    { sessionId: "smoke-sandbox" },
  )) as SandboxToolResult;
  console.log({
    stdout: r1.stdout?.trim(),
    exitCode: r1.exitCode,
    durationMs: r1.durationMs,
    provider: r1.provider,
  });
  if (r1.stdout?.trim() !== "4950") {
    throw new Error(`case 1 失败：期望 4950，实际 ${r1.stdout}`);
  }

  // ────────────────────────────────────────────────────────────────────
  // Case 2：node 斐波那契
  // ────────────────────────────────────────────────────────────────────
  divider("Case 2 · node fib(10) 期望 [0,1,1,2,3,5,8,13,21,34]");
  const fibCode = `
    const out = [];
    let a = 0, b = 1;
    for (let i = 0; i < 10; i++) { out.push(a); [a, b] = [b, a + b]; }
    console.log(JSON.stringify(out));
  `;
  const r2 = (await wrapped.execute(
    { code: fibCode, language: "node", timeoutMs: 5_000 },
    { sessionId: "smoke-sandbox" },
  )) as SandboxToolResult;
  console.log({
    stdout: r2.stdout?.trim(),
    exitCode: r2.exitCode,
    durationMs: r2.durationMs,
  });
  const expectedFib = "[0,1,1,2,3,5,8,13,21,34]";
  if (r2.stdout?.trim() !== expectedFib) {
    throw new Error(`case 2 失败：期望 ${expectedFib}，实际 ${r2.stdout}`);
  }

  // ────────────────────────────────────────────────────────────────────
  // Case 3：超时被 SIGKILL
  // ────────────────────────────────────────────────────────────────────
  divider("Case 3 · 超时被 SIGKILL 期望 timedOut=true");
  const r3 = (await wrapped.execute(
    {
      code: "setInterval(() => {}, 1000)",
      language: "node",
      timeoutMs: 300,
    },
    { sessionId: "smoke-sandbox" },
  )) as SandboxToolResult;
  console.log({
    timedOut: r3.timedOut,
    exitCode: r3.exitCode,
    durationMs: r3.durationMs,
  });
  if (!r3.timedOut) {
    throw new Error(`case 3 失败：期望 timedOut=true，实际 ${JSON.stringify(r3)}`);
  }

  // ────────────────────────────────────────────────────────────────────
  // Case 4：permission ask 路径 —— 超 60s 被 PermissionEngine 挡住
  // ────────────────────────────────────────────────────────────────────
  divider("Case 4 · timeoutMs=120000 期望 permission ask 挡住");
  const r4 = (await wrapped.execute(
    {
      code: "console.log('should not run')",
      language: "node",
      timeoutMs: 120_000,
    },
    { sessionId: "smoke-sandbox" },
  )) as SandboxToolResult;
  console.log({ isError: r4.isError, deniedBy: r4.deniedBy });
  if (r4.deniedBy !== "permission-ask-pending") {
    throw new Error(`case 4 失败：期望 deniedBy=permission-ask-pending，实际 ${JSON.stringify(r4)}`);
  }

  divider("✅ Sandbox spike 端到端通过 —— Task #5 验收");
}

main().catch((err) => {
  console.error("smoke-sandbox 失败:", err);
  process.exit(1);
});

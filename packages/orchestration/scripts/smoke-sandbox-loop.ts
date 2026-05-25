/**
 * Sandbox 三道闭环端到端 demo —— Phase B 验收点。
 *
 * 演示 ADR-0020 "三道沙盒"完整流程，以 **evolution-loop 雏形** 为例：
 *
 *   LLM "生成" 策略源码 → AST 审计 → 沙盒跑 → strategy_v1 契约校验 → 拿 signals
 *
 * 用法：
 *
 *   pnpm smoke:sandbox-loop
 *
 * 6 个 case 覆盖三道每个失败路径 + 三道全过 happy path：
 *
 *   1. raw 模式三道全过（向后兼容 spike smoke）
 *   2. 第一道挡：危险 import（os）
 *   3. 第一道挡：缺 generate_signals
 *   4. 第二道挡：python 运行时崩
 *   5. 第三道挡：strategy_v1 + 输出不是 JSON
 *   6. 三道全过 + strategy_v1：拿到 parsed.signals
 */
import { wireToolList } from "../src/mastra/wired-tools.js";
import { sandboxRunCodeTool } from "../src/tools/index.js";

type ToolResult = {
  ok?: boolean;
  stage?: string;
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  parsed?: { version?: string; signals?: Array<{ ts: number; side: string; qty: number }> };
  auditErrors?: string[];
  contractErrors?: string[];
  auditDurationMs?: number;
  durationMs?: number;
};

function divider(title: string): void {
  console.log("\n" + "─".repeat(70));
  console.log("  " + title);
  console.log("─".repeat(70));
}

function expectOk(out: ToolResult, expectedStage: string): void {
  if (!out.ok || out.stage !== expectedStage) {
    throw new Error(`expected ok + stage=${expectedStage}, got: ${JSON.stringify(out)}`);
  }
}

function expectFail(out: ToolResult, expectedStage: string): void {
  if (out.ok !== false || out.stage !== expectedStage) {
    throw new Error(`expected ok=false + stage=${expectedStage}, got: ${JSON.stringify(out)}`);
  }
}

async function main(): Promise<void> {
  const [wrapped] = wireToolList([sandboxRunCodeTool]);
  if (!wrapped?.execute) throw new Error("sandbox.run_code tool not wired");

  // ────────────────────────────────────────────────────────────────────
  // 1. raw 模式三道全过（向后兼容，等价于原 spike smoke）
  // ────────────────────────────────────────────────────────────────────
  divider("Case 1 · python + raw + 安全 import → 三道全过 → stage=done");
  const r1 = (await wrapped.execute({
    code: "import math\nprint(math.factorial(5))",
    language: "python",
    timeoutMs: 10_000,
    contractSchema: "raw",
  })) as ToolResult;
  console.log({
    ok: r1.ok,
    stage: r1.stage,
    stdout: r1.stdout?.trim(),
    auditDurationMs: r1.auditDurationMs?.toFixed(1),
    executeDurationMs: r1.durationMs?.toFixed(1),
  });
  expectOk(r1, "done");
  if (r1.stdout?.trim() !== "120") throw new Error("case 1 stdout mismatch");

  // ────────────────────────────────────────────────────────────────────
  // 2. 第一道挡：危险 import
  // ────────────────────────────────────────────────────────────────────
  divider("Case 2 · 第一道挡：import os → stage=audit");
  const r2 = (await wrapped.execute({
    code: "import os\nprint(os.getcwd())",
    language: "python",
    timeoutMs: 5_000,
  })) as ToolResult;
  console.log({ ok: r2.ok, stage: r2.stage, auditErrors: r2.auditErrors });
  expectFail(r2, "audit");

  // ────────────────────────────────────────────────────────────────────
  // 3. 第一道挡：缺 generate_signals（evolution-loop 结构性要求）
  // ────────────────────────────────────────────────────────────────────
  divider("Case 3 · 第一道挡：缺 generate_signals → stage=audit");
  const r3 = (await wrapped.execute({
    code: "import math\nprint(math.pi)",
    language: "python",
    timeoutMs: 5_000,
    requireFunctions: ["generate_signals"],
  })) as ToolResult;
  console.log({ ok: r3.ok, stage: r3.stage, auditErrors: r3.auditErrors });
  expectFail(r3, "audit");

  // ────────────────────────────────────────────────────────────────────
  // 4. 第二道挡：python 运行时崩
  // ────────────────────────────────────────────────────────────────────
  divider("Case 4 · 第二道挡：python raise → stage=execute");
  const r4 = (await wrapped.execute({
    code: "raise ValueError('boom')",
    language: "python",
    timeoutMs: 5_000,
  })) as ToolResult;
  console.log({ ok: r4.ok, stage: r4.stage, exitCode: r4.exitCode, stderr: r4.stderr?.split("\n").pop() });
  expectFail(r4, "execute");

  // ────────────────────────────────────────────────────────────────────
  // 5. 第三道挡：strategy_v1 + 输出非 JSON
  // ────────────────────────────────────────────────────────────────────
  divider("Case 5 · 第三道挡：contract=strategy_v1 + 输出非 JSON → stage=contract");
  const r5 = (await wrapped.execute({
    code: "print('not json')",
    language: "python",
    timeoutMs: 5_000,
    contractSchema: "strategy_v1",
  })) as ToolResult;
  console.log({ ok: r5.ok, stage: r5.stage, contractErrors: r5.contractErrors, stdout: r5.stdout?.trim() });
  expectFail(r5, "contract");

  // ────────────────────────────────────────────────────────────────────
  // 6. 三道全过 + strategy_v1：evolution-loop 真闭环
  // ────────────────────────────────────────────────────────────────────
  divider("Case 6 · evolution-loop 雏形：generate_signals + strategy_v1 → parsed.signals");
  // 注意：LocalSubprocessProvider 用 system python3，**不继承 paper service 的 venv**。
  // 想用 numpy/pandas/scipy 要么 system 装好，要么切 DaytonaProvider 用预装镜像。
  // 这里用 stdlib statistics 演示，证明三道闭环本身工作。
  const evolutionCode = `
import json
import statistics

def generate_signals(bars):
    # 模拟 LLM 写的"信号生成函数"——简单均值对比占位
    fast = statistics.mean([1.0, 2.0, 3.0])
    slow = statistics.mean([0.5, 1.0, 1.5, 2.0])
    if fast > slow:
        return [{"ts": 1700000000000, "side": "BUY", "qty": 0.01}]
    return []

result = {"version": "strategy_v1", "signals": generate_signals([])}
print(json.dumps(result))
`.trim();

  const r6 = (await wrapped.execute({
    code: evolutionCode,
    language: "python",
    timeoutMs: 10_000,
    contractSchema: "strategy_v1",
    requireFunctions: ["generate_signals"],
  })) as ToolResult;
  console.log({
    ok: r6.ok,
    stage: r6.stage,
    parsed: r6.parsed,
    auditDurationMs: r6.auditDurationMs?.toFixed(1),
    executeDurationMs: r6.durationMs?.toFixed(1),
  });
  expectOk(r6, "done");
  if (r6.parsed?.version !== "strategy_v1") throw new Error("case 6 parsed version mismatch");
  if (!r6.parsed.signals || r6.parsed.signals.length !== 1) throw new Error("case 6 signals mismatch");

  divider("✅ Phase B 三道闭环端到端通过 —— ADR-0020 完整");
}

main().catch((err) => {
  console.error("smoke-sandbox-loop 失败:", err);
  process.exit(1);
});

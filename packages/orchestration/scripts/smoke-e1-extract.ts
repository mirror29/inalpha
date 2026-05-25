/**
 * E1 闭环 · 第 1 步：sandbox 抽 signals 到 stdout（纯 JSON）。
 *
 * 不打装饰、不带 log —— 给跨语言 bridge 用：
 *
 *   pnpm tsx scripts/smoke-e1-extract.ts | python_smoke_e1_replay.py
 *
 * 链路：
 *   LLM-style python 源码 → AST 审计 → LocalSubprocessProvider 跑 → strategy_v1 校验
 *   → parsed.signals → stdout JSON
 *
 * 出错时 process.exit(1) + 把错误塞 stderr（bash 能 pipe-fail）。
 */
import { wireToolList } from "../src/mastra/wired-tools.js";
import { sandboxRunCodeTool } from "../src/tools/index.js";

const EVOLUTION_CODE = `
import json
import statistics

def generate_signals(bars):
    """模拟 LLM 生成的策略：上升趋势 BUY，回落 SELL。"""
    # 真实 evolution loop 会让 LLM 拿到 bars 参数；这里 spike 用占位
    out = []
    # 假设 bars 是 [{ts, close}] —— spike 阶段直接用 hardcode signal
    out.append({"ts": 1_700_010_800_000, "side": "BUY", "qty": 0.5})   # 第 4 根 bar
    out.append({"ts": 1_700_025_200_000, "side": "SELL", "qty": 0.5})  # 第 8 根 bar
    return out

result = {"version": "strategy_v1", "signals": generate_signals([])}
print(json.dumps(result))
`.trim();

type ToolResult = {
  ok?: boolean;
  stage?: string;
  parsed?: { version: string; signals: Array<{ ts: number; side: string; qty: number }> };
  auditErrors?: string[];
  contractErrors?: string[];
  stderr?: string;
};

async function main(): Promise<void> {
  const [wrapped] = wireToolList([sandboxRunCodeTool]);
  if (!wrapped?.execute) {
    process.stderr.write("sandbox.run_code tool not wired\n");
    process.exit(1);
  }

  const out = (await wrapped.execute({
    code: EVOLUTION_CODE,
    language: "python",
    timeoutMs: 10_000,
    contractSchema: "strategy_v1",
    requireFunctions: ["generate_signals"],
  })) as ToolResult;

  if (!out.ok || out.stage !== "done" || !out.parsed) {
    process.stderr.write(
      `E1 extract failed: stage=${out.stage}\n` +
        `audit: ${JSON.stringify(out.auditErrors)}\n` +
        `contract: ${JSON.stringify(out.contractErrors)}\n` +
        `stderr: ${out.stderr ?? ""}\n`,
    );
    process.exit(1);
  }

  // stdout 只输出 parsed JSON，给下游 python 消费
  process.stdout.write(JSON.stringify(out.parsed));
}

main().catch((err) => {
  process.stderr.write(`E1 extract crashed: ${err.message}\n`);
  process.exit(1);
});

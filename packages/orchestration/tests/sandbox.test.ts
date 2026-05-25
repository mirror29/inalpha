/**
 * SandboxProvider 单元测试 —— Task #2 验收。
 *
 * 跑 node 走 LocalSubprocessProvider，验证：
 * - happy path（stdout / exitCode）
 * - 超时（timedOut + SIGKILL）
 * - 非零退出（exitCode 透传）
 * - 输出截断（truncated）
 * - factory env 切换
 * - spawn error（命令不存在）
 */
import { afterEach, describe, expect, it } from "vitest";

import { HookRunner } from "../src/hooks/index.js";
import { wireToolList } from "../src/mastra/wired-tools.js";
import { DEFAULT_PERMISSIONS, PermissionEngine } from "../src/permissions/index.js";
import {
  LocalSubprocessProvider,
  auditCode,
  getSandboxProvider,
  resetSandboxProvider,
  verifyContract,
} from "../src/sandbox/index.js";
import {
  allTools,
  sandboxRunCodeTool,
} from "../src/tools/index.js";

describe("LocalSubprocessProvider", () => {
  const provider = new LocalSubprocessProvider();

  it("跑 node 一行 print，stdout 拿到结果，exitCode 0", async () => {
    const result = await provider.execute({
      code: "console.log(1 + 1)",
      language: "node",
    });
    expect(result.exitCode).toBe(0);
    expect(result.stdout.trim()).toBe("2");
    expect(result.stderr).toBe("");
    expect(result.timedOut).toBe(false);
    expect(result.truncated).toBe(false);
    expect(result.provider).toBe("local-subprocess");
    expect(result.durationMs).toBeGreaterThan(0);
  });

  it("超时被 SIGKILL，timedOut=true，exitCode 非 0", async () => {
    const result = await provider.execute({
      code: "setInterval(() => {}, 1000)", // 永远不退出
      language: "node",
      timeoutMs: 200,
    });
    expect(result.timedOut).toBe(true);
    expect(result.exitCode).not.toBe(0);
    expect(result.durationMs).toBeGreaterThanOrEqual(200);
    expect(result.durationMs).toBeLessThan(2000); // SIGKILL 兜底应快于 2s
  });

  it("脚本主动 exit(7)，exitCode 透传", async () => {
    const result = await provider.execute({
      code: "process.exit(7)",
      language: "node",
    });
    expect(result.exitCode).toBe(7);
    expect(result.timedOut).toBe(false);
  });

  it("stderr 单独收集，不污染 stdout", async () => {
    const result = await provider.execute({
      code: "console.log('out'); console.error('err'); process.exit(0)",
      language: "node",
    });
    expect(result.stdout.trim()).toBe("out");
    expect(result.stderr.trim()).toBe("err");
  });

  it("超过 maxOutputBytes 触发 truncated", async () => {
    // node 打 200KB 数据，限制 1KB
    const result = await provider.execute({
      code: "process.stdout.write('x'.repeat(200_000))",
      language: "node",
      maxOutputBytes: 1024,
    });
    expect(result.truncated).toBe(true);
    expect(result.stdout.length).toBeLessThanOrEqual(1024);
  });

  it("env 最小化：沙盒拿不到父进程的自定义环境变量", async () => {
    process.env.INALPHA_SANDBOX_LEAK_CANARY = "should-not-leak";
    try {
      const result = await provider.execute({
        code: "console.log(process.env.INALPHA_SANDBOX_LEAK_CANARY ?? 'undefined')",
        language: "node",
      });
      expect(result.stdout.trim()).toBe("undefined");
    } finally {
      delete process.env.INALPHA_SANDBOX_LEAK_CANARY;
    }
  });
});

describe("getSandboxProvider factory", () => {
  afterEach(() => {
    resetSandboxProvider();
    delete process.env.SANDBOX_PROVIDER;
  });

  it("默认（未设 env）返回 LocalSubprocessProvider", () => {
    const p = getSandboxProvider();
    expect(p.name).toBe("local-subprocess");
  });

  it("SANDBOX_PROVIDER=local 也返回 LocalSubprocessProvider", () => {
    process.env.SANDBOX_PROVIDER = "local";
    const p = getSandboxProvider();
    expect(p.name).toBe("local-subprocess");
  });

  it("daytona 未实现：抛带 Task 编号的错误", () => {
    process.env.SANDBOX_PROVIDER = "daytona";
    expect(() => getSandboxProvider()).toThrow(/暂未实现/);
  });

  it("未知 provider 抛错", () => {
    process.env.SANDBOX_PROVIDER = "wat";
    expect(() => getSandboxProvider()).toThrow(/unknown SANDBOX_PROVIDER/);
  });

  it("缓存生效：第二次调用返回同一实例", () => {
    const a = getSandboxProvider();
    const b = getSandboxProvider();
    expect(a).toBe(b);
  });
});

describe("sandbox.run_code tool · wireToolList integration", () => {
  afterEach(() => {
    resetSandboxProvider();
  });

  it("已注册到 allTools，id 是 sandbox.run_code", () => {
    const ids = allTools.map((t) => t.id);
    expect(ids).toContain("sandbox.run_code");
  });

  it("60s 内 timeoutMs 走 allow，真的跑代码并返回 stdout", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool], {
      hookRunner: new HookRunner(),
      permissionEngine: new PermissionEngine(DEFAULT_PERMISSIONS),
    });

    const out = (await wrapped!.execute!({
      code: "console.log('hello-sandbox-spike')",
      language: "node",
      timeoutMs: 5_000,
    })) as { stdout: string; exitCode: number };

    expect(out.exitCode).toBe(0);
    expect(out.stdout).toContain("hello-sandbox-spike");
  });

  it("超 60s timeoutMs 命中 ask predicate，返回 permission-ask-pending", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool], {
      hookRunner: new HookRunner(),
      permissionEngine: new PermissionEngine(DEFAULT_PERMISSIONS),
    });

    const out = (await wrapped!.execute!({
      code: "console.log('not reached')",
      language: "node",
      timeoutMs: 120_000, // > 60_000 predicate threshold
    })) as { isError: boolean; deniedBy: string };

    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission-ask-pending");
  });
});

// ────────────────────────────────────────────────────────────────────
// 第一道：AST 审计
// ────────────────────────────────────────────────────────────────────

describe("auditCode（第一道）", () => {
  it("node 跳过审计：始终返回 ok=true，duration=0", async () => {
    const r = await auditCode("require('fs').readFileSync('/etc/passwd')", { language: "node" });
    expect(r.ok).toBe(true);
    expect(r.errors).toHaveLength(0);
    expect(r.durationMs).toBe(0);
  });

  it("python 合法白名单代码通过", async () => {
    const code = `
import math
import numpy as np

def calc():
    return math.sqrt(np.sum([1, 2, 3]))

print(calc())
`.trim();
    const r = await auditCode(code, { language: "python" });
    expect(r.ok).toBe(true);
    expect(r.errors).toHaveLength(0);
  });

  it("python 危险 import (os) 被拒", async () => {
    const r = await auditCode("import os\nprint(os.listdir('/'))", { language: "python" });
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/denied import.*os/);
  });

  it("python 危险 import (subprocess) 被拒", async () => {
    const r = await auditCode("from subprocess import run\nrun(['ls'])", { language: "python" });
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/denied import.*subprocess/);
  });

  it("python 调 exec / eval 被拒", async () => {
    const r1 = await auditCode("exec('print(1)')", { language: "python" });
    expect(r1.ok).toBe(false);
    expect(r1.errors.join("\n")).toMatch(/denied call.*exec/);

    const r2 = await auditCode("eval('1+1')", { language: "python" });
    expect(r2.ok).toBe(false);
    expect(r2.errors.join("\n")).toMatch(/denied call.*eval/);
  });

  it("python 危险 dunder 访问（沙盒越狱）被拒", async () => {
    const r = await auditCode(
      "().__class__.__bases__[0].__subclasses__()",
      { language: "python" },
    );
    expect(r.ok).toBe(false);
    expect(r.errors.some((e) => /__class__|__bases__|__subclasses__/.test(e))).toBe(true);
  });

  it("python SyntaxError 被拒（带行号）", async () => {
    const r = await auditCode("def broken(:\n    pass", { language: "python" });
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/SyntaxError/);
  });

  it("requireFunctions 缺失被拒（evolution loop 用例）", async () => {
    const r = await auditCode("import math\nprint(math.pi)", {
      language: "python",
      requireFunctions: ["generate_signals"],
    });
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/missing required.*generate_signals/);
  });

  it("requireFunctions 满足通过", async () => {
    const code = `
import numpy as np

def generate_signals(bars):
    return []
`.trim();
    const r = await auditCode(code, {
      language: "python",
      requireFunctions: ["generate_signals"],
    });
    expect(r.ok).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────
// 第三道：协议契约
// ────────────────────────────────────────────────────────────────────

describe("verifyContract（第三道）", () => {
  it("raw 模式始终 ok，不解析 stdout", () => {
    const r = verifyContract("raw", "这不是 JSON 但 raw 模式不在乎");
    expect(r.ok).toBe(true);
    expect(r.parsed).toBeUndefined();
  });

  it("strategy_v1：合法 JSON + 合法 schema 通过", () => {
    const stdout = JSON.stringify({
      version: "strategy_v1",
      signals: [
        { ts: 1700000000000, side: "BUY", qty: 0.01 },
        { ts: 1700000100000, side: "SELL", qty: 0.01 },
      ],
    });
    const r = verifyContract("strategy_v1", stdout);
    expect(r.ok).toBe(true);
    expect(r.parsed).toMatchObject({
      version: "strategy_v1",
      signals: expect.arrayContaining([
        expect.objectContaining({ side: "BUY" }),
      ]),
    });
  });

  it("strategy_v1：空 stdout 拒绝", () => {
    const r = verifyContract("strategy_v1", "   \n  ");
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/empty/);
  });

  it("strategy_v1：非 JSON 拒绝", () => {
    const r = verifyContract("strategy_v1", "hello world");
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/not valid JSON/);
  });

  it("strategy_v1：缺 version 字段被 zod 拒", () => {
    const r = verifyContract("strategy_v1", JSON.stringify({ signals: [] }));
    expect(r.ok).toBe(false);
    expect(r.errors.length).toBeGreaterThan(0);
  });

  it("strategy_v1：signal.side 非枚举值被拒", () => {
    const stdout = JSON.stringify({
      version: "strategy_v1",
      signals: [{ ts: 1, side: "HOLD", qty: 1 }],
    });
    const r = verifyContract("strategy_v1", stdout);
    expect(r.ok).toBe(false);
    expect(r.errors.join("\n")).toMatch(/side|signals/);
  });

  it("strategy_v1：qty 必须 > 0", () => {
    const stdout = JSON.stringify({
      version: "strategy_v1",
      signals: [{ ts: 1, side: "BUY", qty: 0 }],
    });
    const r = verifyContract("strategy_v1", stdout);
    expect(r.ok).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// 三道闭环：sandbox.run_code tool 集成
// ────────────────────────────────────────────────────────────────────

describe("sandbox.run_code · 三道闭环集成", () => {
  afterEach(() => {
    resetSandboxProvider();
  });

  it("三道全过：python + raw + audit pass + execute success", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool]);
    const out = (await wrapped!.execute!({
      code: "import math\nprint(math.factorial(5))",
      language: "python",
      timeoutMs: 10_000,
      contractSchema: "raw",
    })) as { ok: boolean; stage: string; stdout: string; auditDurationMs: number };

    expect(out.ok).toBe(true);
    expect(out.stage).toBe("done");
    expect(out.stdout.trim()).toBe("120");
    expect(out.auditDurationMs).toBeGreaterThan(0); // python 真审计了
  });

  it("第一道 audit 拒：危险 import 短路返回 stage=audit", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool]);
    const out = (await wrapped!.execute!({
      code: "import os\nprint(os.getcwd())",
      language: "python",
      timeoutMs: 5_000,
    })) as { ok: boolean; stage: string; auditErrors: string[] };

    expect(out.ok).toBe(false);
    expect(out.stage).toBe("audit");
    expect(out.auditErrors.join("\n")).toMatch(/denied import.*os/);
  });

  it("第二道 execute 拒：python 运行时崩 → stage=execute", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool]);
    const out = (await wrapped!.execute!({
      code: "raise ValueError('boom')",
      language: "python",
      timeoutMs: 5_000,
    })) as { ok: boolean; stage: string; exitCode: number; stderr: string };

    expect(out.ok).toBe(false);
    expect(out.stage).toBe("execute");
    expect(out.exitCode).not.toBe(0);
    expect(out.stderr).toMatch(/ValueError.*boom/);
  });

  it("第三道 contract 拒：strategy_v1 + 非 JSON 输出 → stage=contract", async () => {
    const [wrapped] = wireToolList([sandboxRunCodeTool]);
    const out = (await wrapped!.execute!({
      code: "print('not a json')",
      language: "python",
      timeoutMs: 5_000,
      contractSchema: "strategy_v1",
    })) as { ok: boolean; stage: string; contractErrors: string[]; stdout: string };

    expect(out.ok).toBe(false);
    expect(out.stage).toBe("contract");
    expect(out.contractErrors.join("\n")).toMatch(/not valid JSON/);
    expect(out.stdout.trim()).toBe("not a json"); // 原 stdout 保留
  });

  it("三道全过 + strategy_v1：evolution-loop 雏形端到端", async () => {
    // 模拟 LLM 生成的策略：定义 generate_signals + 输出 strategy_v1 JSON
    const code = `
import json

def generate_signals(bars):
    return [{"ts": 1700000000000, "side": "BUY", "qty": 0.01}]

result = {"version": "strategy_v1", "signals": generate_signals([])}
print(json.dumps(result))
`.trim();

    const [wrapped] = wireToolList([sandboxRunCodeTool]);
    const out = (await wrapped!.execute!({
      code,
      language: "python",
      timeoutMs: 10_000,
      contractSchema: "strategy_v1",
      requireFunctions: ["generate_signals"],
    })) as {
      ok: boolean;
      stage: string;
      parsed: { version: string; signals: Array<{ side: string; qty: number }> };
    };

    expect(out.ok).toBe(true);
    expect(out.stage).toBe("done");
    expect(out.parsed.version).toBe("strategy_v1");
    expect(out.parsed.signals).toHaveLength(1);
    expect(out.parsed.signals[0]!.side).toBe("BUY");
  });
});

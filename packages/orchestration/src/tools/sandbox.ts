/**
 * sandbox.* 的 Mastra tool 包装 —— ADR-0020 **三道沙盒**完整闭环。
 *
 * 执行流（每一道失败立刻短路返回，附带 stage 字段）：
 *
 *   1. PermissionEngine → predicate `sandbox.run_code(timeoutMs<=60000)` allow，超长 ask
 *   2. **第一道** `audit.auditCode()` → AST 白名单 / 危险节点 / requireFunctions
 *   3. **第二道** `SandboxProvider.execute()` → 进程级隔离 + 超时 SIGKILL（默认 LocalSubprocess）
 *   4. **第三道** `contracts.verifyContract()` → 返回值 schema 校验（raw 跳过）
 *
 * **三道是分层防御**，任一道失败就拒绝；通过则附 `parsed` 解析结果。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { auditCode } from "../sandbox/audit.js";
import { ContractKindSchema, verifyContract } from "../sandbox/contracts.js";
import { getSandboxProvider } from "../sandbox/index.js";
import { DEFAULT_SANDBOX_TIMEOUT_MS } from "../sandbox/provider.js";

const LanguageSchema = z.enum(["python", "node"]);

export const sandboxRunCodeTool = createTool({
  id: "sandbox.run_code",
  description: `
    在受控沙盒里跑一段代码，过 **ADR-0020 三道防御**（AST 审计 → 进程隔离 → 协议校验）。

    何时用：
    - 算一次性数值 / 临时公式（用 contractSchema:'raw' 默认）
    - 跑用户给的小脚本验证想法
    - E1 evolution loop 里跑 LLM 生成的策略源码（contractSchema:'strategy_v1' + requireFunctions:['generate_signals']）

    何时不用：
    - 长时回测 → 用 paper.run_backtest（专用引擎 + 血缘）
    - 需要外部数据 → 沙盒 env 拿不到 data-service / DB / API key
    - >60s 任务 → permission 走 ask 路径

    返回字段：
    - ok: boolean —— 三道全过才 true
    - stage: 'audit' | 'execute' | 'contract' | 'done' —— 失败时定位卡在哪
    - auditErrors / contractErrors —— 各自阶段错误列表
    - parsed —— contract 校验通过后的解析对象（strategy_v1 时 = {version, signals}）
    - stdout / stderr / exitCode / timedOut / truncated / durationMs / provider —— sandbox 原始输出

    坑：
    - python 代码必须只 import 白名单（math/numpy/pandas/scipy/statistics/collections/itertools/...）
    - node 当前**不审计**（仅靠运行时隔离）；strategy_v1 contract 也不限制语言
    - contractSchema='strategy_v1' 时 stdout **必须**是一行 JSON：{"version":"strategy_v1","signals":[...]}
  `.trim(),
  inputSchema: z.object({
    code: z.string().min(1).describe("待执行源码"),
    language: LanguageSchema.describe("python 走 python3 -c；node 走 node -e（node 跳审计）"),
    timeoutMs: z
      .number()
      .int()
      .min(100)
      .max(300_000)
      .default(DEFAULT_SANDBOX_TIMEOUT_MS)
      .describe("超时（毫秒），默认 30000；>60000 走 permission ask"),
    contractSchema: ContractKindSchema.default("raw").describe(
      "返回值契约：raw=不校验；strategy_v1=要求 stdout 是 JSON {version:'strategy_v1',signals:[...]}",
    ),
    requireFunctions: z
      .array(z.string())
      .optional()
      .describe(
        "AST 审计强制定义的顶层函数名（evolution loop 用 ['generate_signals']）",
      ),
  }),
  execute: async (inputData) => {
    // ──────────────────────────────────────────
    // 第一道：AST 审计（python 真审；node 跳过）
    // ──────────────────────────────────────────
    const audit = await auditCode(inputData.code, {
      language: inputData.language,
      requireFunctions: inputData.requireFunctions,
    });
    if (!audit.ok) {
      return {
        ok: false,
        stage: "audit" as const,
        auditErrors: audit.errors,
        auditDurationMs: audit.durationMs,
      };
    }

    // ──────────────────────────────────────────
    // 第二道：进程隔离执行
    // ──────────────────────────────────────────
    const provider = getSandboxProvider();
    const exec = await provider.execute({
      code: inputData.code,
      language: inputData.language,
      timeoutMs: inputData.timeoutMs,
    });

    // 执行本身炸了（非 0 exit / 超时 / spawn 错）→ 不再走 contract，直接报告
    if (exec.exitCode !== 0 || exec.timedOut) {
      return {
        ok: false,
        stage: "execute" as const,
        auditDurationMs: audit.durationMs,
        ...exec,
      };
    }

    // ──────────────────────────────────────────
    // 第三道：契约校验（raw 直接通过）
    // ──────────────────────────────────────────
    // zod `.default()` 在 input 类型上保留 optional —— 跟 paper.ts 同样 quirk，手动兜底
    const contract = verifyContract(inputData.contractSchema ?? "raw", exec.stdout);
    if (!contract.ok) {
      return {
        ok: false,
        stage: "contract" as const,
        contractErrors: contract.errors,
        auditDurationMs: audit.durationMs,
        ...exec,
      };
    }

    return {
      ok: true,
      stage: "done" as const,
      auditDurationMs: audit.durationMs,
      parsed: contract.parsed,
      ...exec,
    };
  },
});

export const sandboxTools = [sandboxRunCodeTool] as const;

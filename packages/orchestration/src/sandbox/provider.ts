/**
 * SandboxProvider —— ADR-0020 第二道 "运行隔离" 的抽象层。
 *
 * Inalpha 的 evolution loop（E1 路线）需要让 LLM 生成的策略源码在受控环境跑回测，
 * 这一层把"在哪跑"和"跑什么"解耦：
 *
 * - **LocalSubprocessProvider**（默认）：node child_process spawn，零外部依赖，零 SaaS key
 * - **DaytonaProvider**（可选）：本地 docker 起 Daytona，microVM 级隔离
 * - **E2BProvider**（可选，未来）：SaaS microVM，需 E2B_API_KEY
 *
 * 切换通过环境变量 `SANDBOX_PROVIDER`（factory.ts 处理）。
 *
 * **不属于本层的事**：
 * - 第一道 AST 静态审计 —— 在 hooks/PreToolUse 里做
 * - 第三道 Strategy 协议契约校验 —— 在 sandbox.run_code tool 的 execute 返回前做
 */

/** 沙盒支持的语言。spike 阶段 python + node；typescript 走 tsx 不便携，留作后续。 */
export type SandboxLanguage = "python" | "node";

/** 沙盒执行入参。 */
export type SandboxExecuteRequest = {
  /** 待执行源码。LLM-generated code 进来前**应已过 AST 审计**。 */
  code: string;
  /** 语言。 */
  language: SandboxLanguage;
  /** 超时（毫秒）；默认 30_000。超时后子进程 SIGKILL。 */
  timeoutMs?: number;
  /** 标准输出截断阈值（字节）；默认 1MB。超过则 result.truncated = true。 */
  maxOutputBytes?: number;
};

/** 沙盒执行结果。 */
export type SandboxExecuteResult = {
  /** 标准输出（已按 maxOutputBytes 截断）。 */
  stdout: string;
  /** 标准错误（同上）。 */
  stderr: string;
  /** 子进程 exit code；超时 / 信号杀死时为负数或 null → 这里统一 -1。 */
  exitCode: number;
  /** 是否超时被杀。 */
  timedOut: boolean;
  /** 是否触发输出截断。 */
  truncated: boolean;
  /** wall-clock 耗时（毫秒）。 */
  durationMs: number;
  /** 实际跑这次的 provider 标识（"local-subprocess" / "daytona" / ...）。 */
  provider: string;
};

/**
 * 沙盒提供方抽象。
 *
 * 实现要求：
 * - **stateless**：每次 execute 都是干净环境（不复用前次的变量 / 文件）
 * - **超时强制**：必须遵守 timeoutMs，到点 SIGKILL
 * - **不抛异常**：错误（超时 / 崩溃 / 编译失败）通过 result 字段表达，
 *   只有 provider 自身故障（如 daytona 连不上）才抛
 */
export interface SandboxProvider {
  /** provider 名（用于日志 / metrics / debug）。 */
  readonly name: string;

  /**
   * 在沙盒里跑一段代码。
   *
   * @param req 执行请求
   * @returns 执行结果；**任何业务错误（语法 / 运行时 / 超时）通过 result 表达，不抛异常**
   */
  execute(req: SandboxExecuteRequest): Promise<SandboxExecuteResult>;
}

/** 沙盒默认超时（毫秒）。 */
export const DEFAULT_SANDBOX_TIMEOUT_MS = 30_000;

/** 默认输出截断（字节）；超过则截断 + 标记 truncated。 */
export const DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024;

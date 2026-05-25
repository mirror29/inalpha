/**
 * LocalSubprocessProvider —— SandboxProvider 的默认实现。
 *
 * 通过 `child_process.spawn` 起子进程跑代码。这是 ADR-0020 第二道"子进程隔离"
 * 的最薄实现，零外部依赖（不依赖 docker / SaaS）。
 *
 * **隔离强度**：仅进程级 + env 白名单 + 超时 SIGKILL。**不**做 namespace / cgroups
 * / seccomp —— 想要 microVM 级隔离切到 DaytonaProvider。
 *
 * **设计取舍**：
 * - `spawn(cmd, args, { shell: false })` 避免 shell-injection；code 作为 argv 不被解释
 * - env 只继承 PATH（最小权限），其他敏感变量（API key 等）不漏给沙盒
 * - 超时用 spawn 自带 timeout + 自定义 SIGKILL 双保险（spawn timeout 在某些 Node 版本只发 SIGTERM）
 */
import { spawn } from "node:child_process";
import { performance } from "node:perf_hooks";

import {
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_SANDBOX_TIMEOUT_MS,
  type SandboxExecuteRequest,
  type SandboxExecuteResult,
  type SandboxLanguage,
  type SandboxProvider,
} from "./provider.js";

/** 把 language 翻成 spawn 命令。 */
function resolveCommand(lang: SandboxLanguage): { cmd: string; argsPrefix: string[] } {
  switch (lang) {
    case "python":
      // python3 -c <code>：argv 透传不走 shell，安全
      return { cmd: "python3", argsPrefix: ["-c"] };
    case "node":
      // node -e <code>：跑 JS；CI / 本地 node 一定有
      return { cmd: "node", argsPrefix: ["-e"] };
    default: {
      const _exhaustive: never = lang;
      throw new Error(`unsupported sandbox language: ${_exhaustive as string}`);
    }
  }
}

/**
 * 本地子进程沙盒。
 *
 * **何时用**：默认 spike / 开发 / 单测；零依赖。
 * **何时不用**：跑不可信 LLM-generated code 在生产环境 —— 切 DaytonaProvider 拿 microVM 隔离。
 */
export class LocalSubprocessProvider implements SandboxProvider {
  readonly name = "local-subprocess";

  async execute(req: SandboxExecuteRequest): Promise<SandboxExecuteResult> {
    const start = performance.now();
    const timeoutMs = req.timeoutMs ?? DEFAULT_SANDBOX_TIMEOUT_MS;
    const maxBytes = req.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
    const { cmd, argsPrefix } = resolveCommand(req.language);

    return new Promise<SandboxExecuteResult>((resolve) => {
      // 显式 Buffer<ArrayBufferLike>：Node 24+ @types/node 把 Buffer 改成带泛型，
      // Buffer.alloc(0) 推断 Buffer<ArrayBuffer>，而 stream chunk 是 Buffer<ArrayBufferLike>，
      // 不显式标会让 assign 路径类型不兼容。
      let stdoutBuf: Buffer<ArrayBufferLike> = Buffer.alloc(0);
      let stderrBuf: Buffer<ArrayBufferLike> = Buffer.alloc(0);
      let truncated = false;
      let timedOut = false;
      let settled = false;

      const child = spawn(cmd, [...argsPrefix, req.code], {
        shell: false,
        // 最小 env —— 只继承 PATH，避免泄露 API key 等到沙盒
        env: { PATH: process.env.PATH ?? "" },
        stdio: ["ignore", "pipe", "pipe"],
      });

      const killTimer = setTimeout(() => {
        timedOut = true;
        // 先 SIGTERM 给个机会，500ms 后 SIGKILL 兜底
        child.kill("SIGTERM");
        setTimeout(() => {
          if (!settled) child.kill("SIGKILL");
        }, 500);
      }, timeoutMs);

      const appendOutput = (buf: Buffer, chunk: Buffer): Buffer => {
        const remaining = maxBytes - buf.length;
        if (remaining <= 0) {
          truncated = true;
          return buf;
        }
        if (chunk.length > remaining) {
          truncated = true;
          return Buffer.concat([buf, chunk.subarray(0, remaining)]);
        }
        return Buffer.concat([buf, chunk]);
      };

      child.stdout.on("data", (chunk: Buffer) => {
        stdoutBuf = appendOutput(stdoutBuf, chunk);
      });
      child.stderr.on("data", (chunk: Buffer) => {
        stderrBuf = appendOutput(stderrBuf, chunk);
      });

      const settle = (exitCode: number): void => {
        if (settled) return;
        settled = true;
        clearTimeout(killTimer);
        resolve({
          stdout: stdoutBuf.toString("utf8"),
          stderr: stderrBuf.toString("utf8"),
          exitCode,
          timedOut,
          truncated,
          durationMs: performance.now() - start,
          provider: this.name,
        });
      };

      child.on("error", (err) => {
        // spawn 本身失败（cmd 不存在等）—— 把错误塞 stderr，exitCode = -1
        stderrBuf = appendOutput(stderrBuf, Buffer.from(`spawn error: ${err.message}`));
        settle(-1);
      });

      child.on("close", (code) => {
        settle(code ?? -1);
      });
    });
  }
}

/**
 * SandboxProvider factory —— 根据环境变量 SANDBOX_PROVIDER 返回实现。
 *
 * 支持值：
 * - `local` / `local-subprocess`（默认）—— LocalSubprocessProvider
 * - `daytona`（未实现）—— DaytonaProvider，本地 docker 起 daytona
 * - `e2b`（未实现）—— SaaS，需 E2B_API_KEY
 *
 * **单例缓存**：第一次拿到的 provider 复用整个 process；测试用 `resetSandboxProvider()` 清。
 */
import { LocalSubprocessProvider } from "./local.js";
import type { SandboxProvider } from "./provider.js";

let cached: SandboxProvider | null = null;

/** 拿到当前进程的 SandboxProvider 单例。 */
export function getSandboxProvider(): SandboxProvider {
  if (cached) return cached;
  const kind = (process.env.SANDBOX_PROVIDER ?? "local").toLowerCase();
  switch (kind) {
    case "local":
    case "local-subprocess":
      cached = new LocalSubprocessProvider();
      break;
    case "daytona":
      throw new Error(
        "SANDBOX_PROVIDER=daytona 暂未实现（Task #3）。当前请用 'local' 或留空。",
      );
    case "e2b":
      throw new Error(
        "SANDBOX_PROVIDER=e2b 暂未实现。当前请用 'local' 或留空。",
      );
    default:
      throw new Error(`unknown SANDBOX_PROVIDER='${kind}'；可选 local / daytona / e2b`);
  }
  return cached;
}

/** 清缓存；仅测试用。 */
export function resetSandboxProvider(): void {
  cached = null;
}

/** 显式注入 provider；仅测试 / 集成 dev 用。 */
export function setSandboxProvider(provider: SandboxProvider): void {
  cached = provider;
}

/**
 * dotenv 加载根目录 .env（统一 env 入口）。
 *
 * **Side-effect import** —— 本模块顶层立刻加载、不导出任何符号。
 * 任何使用 `process.env.{LLM_*, DEEPSEEK_*, ANTHROPIC_*, ...}` 的入口
 * 必须把 `import "./env.js"` 放在 import 列表的**最前面**，以保证 dotenv
 * 在模块顶层 `createXxx({ apiKey: process.env.* })` 类调用之前生效。
 *
 * 加载顺序（`override: false`，即"已有 env 不覆盖"）：
 *
 * 1. shell 显式 `export FOO=...` —— 最优先（启动时已在 process.env）
 * 2. `packages/orchestration/.env` —— 旧用户兼容（cwd 无关，经 paths.ts 定位）
 * 3. `<repo-root>/.env` —— **统一入口**（新用户填这里）
 *
 * 与 Python 端 `services/_shared/config.py` 的 `env_file=(<root>/.env, ./.env)`
 * 设计对称。
 */
import { config as loadDotenv } from "dotenv";
import { resolve } from "node:path";

import { resolveOrchestrationRoot, resolveRepoRoot } from "./mastra/paths.js";

let _loaded = false;

/**
 * 显式触发 env 加载（幂等）。
 *
 * 为什么除了 side-effect import 还要这个函数（2026-06-11 实测）：mastra dev 的
 * bundler 会丢掉纯 side-effect 的 `import "../../env.js"`——bundle 后本模块从未
 * 执行、根 .env 从未被加载，此前"能跑"全靠 mastra dev 原生加载 cwd 下的
 * `packages/orchestration/.env`。key 收敛到根 .env 后启动即裸崩
 * （DEEPSEEK_API_KEY is missing）。函数调用无法被 tree-shake——所有读 LLM env
 * 的模块（llm/provider.ts 等）在模块顶层调 `ensureEnvLoaded()`，不再依赖
 * import 顺序与 bundler 行为。
 */
export function ensureEnvLoaded(): void {
  if (_loaded) return;
  _loaded = true;
  // 不能用 process.cwd()（mastra server 子进程 cwd 是 src/mastra/public/），
  // 也不能用 import.meta.url（bundle 后指向 .mastra/output）—— 统一走 paths.ts。
  // override:false → 已有的 process.env 优先；本调用只填空缺
  loadDotenv({ path: resolve(resolveOrchestrationRoot(), ".env"), override: false });
  loadDotenv({ path: resolve(resolveRepoRoot(), ".env"), override: false });
}

// side-effect import 的旧语义保留（vitest setup / 脚本等不经 bundler 的场景）
ensureEnvLoaded();

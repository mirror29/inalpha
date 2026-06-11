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

// 不能用 process.cwd()（mastra server 子进程 cwd 是 src/mastra/public/），
// 也不能用 import.meta.url（bundle 后指向 .mastra/output）—— 统一走 paths.ts。
// override:false → 已有的 process.env 优先；本调用只填空缺
loadDotenv({ path: resolve(resolveOrchestrationRoot(), ".env"), override: false });
loadDotenv({ path: resolve(resolveRepoRoot(), ".env"), override: false });

/**
 * Vitest 全局 setup —— 在任何 test 文件 import 之前跑。
 *
 * 目的：为 ``src/mastra/index.ts`` 这种**模块顶层**调 ``getSettings()`` 的入口
 * 提供合法的 env 兜底。原因：
 *
 * - ``src/mastra/index.ts:80`` 模块顶层执行 ``getSettings().schedulerEnabled``，
 *   import 时即触发 zod 校验
 * - 多数 test 文件在 ``beforeEach`` 里 ``setSettings()`` 覆盖 —— 但 beforeEach
 *   在 ``import`` **之后**才跑，已经太晚
 * - 结果：CI 无 JWT_SECRET env 时，``tests/workflows.{hello,backtest-grid}.test.ts``
 *   在 import 阶段就抛 "jwtSecret: expected string, received undefined"
 *
 * 这里用 ``??=`` 仅在 env 未设时填默认值，不破坏 CI 真实 env / 本地 .env。
 */
process.env.JWT_SECRET ??= "test-secret-32-chars-or-more-xxxxxxx";
process.env.DATA_SERVICE_URL ??= "http://data-mock.test";
process.env.PAPER_SERVICE_URL ??= "http://paper-mock.test";
process.env.RESEARCH_SERVICE_URL ??= "http://research-mock.test";

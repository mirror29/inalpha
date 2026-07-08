/**
 * 环境变量 → typed settings。
 *
 * 跟 services/_shared/config.py 的设计对称：每个 service / package 都有一个
 * 自己的 Settings 函数，启动时 fail-fast 校验必填字段。
 */
import { z } from "zod";

const SettingsSchema = z.object({
  dataServiceUrl: z.string().url().default("http://localhost:8001"),
  paperServiceUrl: z.string().url().default("http://localhost:8002"),
  researchServiceUrl: z.string().url().default("http://localhost:8003"),
  factorServiceUrl: z.string().url().default("http://localhost:8004"),
  evolverServiceUrl: z.string().url().default("http://localhost:8005"),
  jwtSecret: z.string().min(16, "JWT_SECRET must be at least 16 chars"),
  jwtAlgorithm: z.literal("HS256").default("HS256"),
  schedulerEnabled: z.coerce.boolean().default(false),
  databaseUrl: z.string().min(1).optional(),
  // 控制台身份 subject —— agent 后台调后端时的默认账户。默认值刻意对齐 dashboard
  // apps/dashboard/src/lib/backend.ts 的 CONSOLE_SUBJECT 默认值,这样不设任何 env
  // 两边就自动落到同一个 account_id(account_id = uuid5(sub)),agent 写的 live run /
  // 订单 / 候选才能被控制台读到。上多租户时改 CONSOLE_SUBJECT 来源即可两边同步。
  consoleSubject: z.string().min(1).default("console:dev"),
});

export type Settings = z.infer<typeof SettingsSchema>;

let _cached: Settings | undefined;

/**
 * 加载并缓存 settings。第一次调用时校验环境变量。
 *
 * 测试时通过 `setSettings()` 覆盖（避免读真实 env）。
 */
export function getSettings(): Settings {
  if (_cached !== undefined) return _cached;

  const parsed = SettingsSchema.safeParse({
    dataServiceUrl: process.env.DATA_SERVICE_URL,
    paperServiceUrl: process.env.PAPER_SERVICE_URL,
    researchServiceUrl: process.env.RESEARCH_SERVICE_URL,
    factorServiceUrl: process.env.FACTOR_SERVICE_URL,
    evolverServiceUrl: process.env.EVOLVER_SERVICE_URL,
    jwtSecret: process.env.JWT_SECRET,
    jwtAlgorithm: process.env.JWT_ALGORITHM,
    schedulerEnabled: process.env.SCHEDULER_ENABLED,
    databaseUrl: process.env.DATABASE_URL,
    consoleSubject: process.env.CONSOLE_SUBJECT,
  });

  if (!parsed.success) {
    const fmt = parsed.error.issues
      .map((e) => `  ${e.path.join(".")}: ${e.message}`)
      .join("\n");
    throw new Error(`Settings validation failed:\n${fmt}`);
  }

  _cached = parsed.data;
  return _cached;
}

/** 测试时显式注入 settings，绕过环境变量。 */
export function setSettings(settings: Settings): void {
  _cached = settings;
}

/** 测试 / 热重载时清缓存。 */
export function clearSettings(): void {
  _cached = undefined;
}

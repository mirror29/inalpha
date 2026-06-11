/**
 * cwd 无关的路径定位 —— mastra 库 / .env 的单一权威。
 *
 * 为什么存在：``mastra dev`` 的 CLI 父进程 cwd 是 package 根，但真正跑 server 的
 * 子进程 cwd 是 ``src/mastra/public/``（mastra 版本行为，未文档化）。任何
 * ``resolve(process.cwd(), ...)`` 都会随启动方式漂移 —— 历史教训：memory 库被
 * 写到 ``src/mastra/public/.mastra/``，换启动方式后 server 指向新空库，
 * dashboard 历史会话"全没了"。
 *
 * ⚠️ 库目录**绝不能**放 ``.mastra/``：那是 mastra 的 build 目录，每次
 * ``mastra dev`` 启动整目录清掉重建 —— 2026-06-11 一次重启清掉了 30MB 聊天
 * 历史库（不可恢复）。数据一律放 ``.data/``（mastra 不感知）。
 *
 * 何时用：orchestration 内所有"相对 package 根 / 仓库根"的文件路径
 * （SQLite 库、.env）一律经由本模块解析。
 *
 * 何时不用：临时文件 / 显式绝对路径配置项。
 *
 * 坑：
 *
 * - ``.mastra/output/package.json``（mastra bundle 产物）name 是 ``"server"``，
 *   向上找 package 根时必须校验 name，否则从 bundle 目录出发会误中
 * - bundle 后 ``import.meta.url`` 指向 ``.mastra/output``，不能当锚点；
 *   以 env 优先 + cwd 向上搜兜底
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const PACKAGE_NAME = "@inalpha/orchestration";

/** 显式锚点 env —— package.json dev script 注入，子进程继承，免疫 cwd 漂移。 */
const ROOT_ENV = "INALPHA_ORCH_ROOT";

let cachedRoot: string | undefined;
let dbDirLogged = false;

function isOrchestrationRoot(dir: string): boolean {
  const pkgPath = join(dir, "package.json");
  if (!existsSync(pkgPath)) return false;
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as {
      name?: unknown;
    };
    return pkg.name === PACKAGE_NAME;
  } catch {
    return false;
  }
}

/**
 * 定位 ``packages/orchestration`` 根目录（绝对路径）。
 *
 * 解析顺序：``INALPHA_ORCH_ROOT`` env > 从 ``process.cwd()`` 向上找
 * name=``@inalpha/orchestration`` 的 package.json > fallback cwd（warn）。
 */
export function resolveOrchestrationRoot(): string {
  if (cachedRoot !== undefined) return cachedRoot;

  const fromEnv = process.env[ROOT_ENV];
  if (fromEnv && isOrchestrationRoot(resolve(fromEnv))) {
    cachedRoot = resolve(fromEnv);
    return cachedRoot;
  }

  let dir = process.cwd();
  for (;;) {
    if (isOrchestrationRoot(dir)) {
      cachedRoot = dir;
      return cachedRoot;
    }
    const parent = dirname(dir);
    if (parent === dir) break; // 到文件系统根了
    dir = parent;
  }

  console.warn(
    `[paths] 未能从 cwd=${process.cwd()} 向上定位 ${PACKAGE_NAME} 根 —— ` +
      `fallback 用 cwd。库路径可能漂移，请设置 ${ROOT_ENV}。`,
  );
  cachedRoot = process.cwd();
  return cachedRoot;
}

/** 仓库根（``packages/orchestration`` 的上两级）—— 统一 .env 入口在这里。 */
export function resolveRepoRoot(): string {
  return resolve(resolveOrchestrationRoot(), "..", "..");
}

/**
 * mastra SQLite 库目录：``<orchestration 根>/.data``（gitignored），不存在则建。
 * 不能用 ``.mastra/``（mastra build 目录，启动即清，见模块头注释）。
 * 首次调用 log 实际路径 —— "历史去哪了"类排查的第一证据。
 */
export function resolveMastraDbDir(): string {
  const dbDir = resolve(resolveOrchestrationRoot(), ".data");
  if (!existsSync(dbDir)) {
    mkdirSync(dbDir, { recursive: true });
  }
  if (!dbDirLogged) {
    dbDirLogged = true;
    console.log(`[paths] mastra db dir: ${dbDir} (cwd=${process.cwd()})`);
  }
  return dbDir;
}

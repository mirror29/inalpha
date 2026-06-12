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
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";

const PACKAGE_NAME = "@inalpha/orchestration";

/** 显式锚点 env —— package.json dev script 注入，子进程继承，免疫 cwd 漂移。 */
const ROOT_ENV = "INALPHA_ORCH_ROOT";

/** 备份保留天数（ADR-0048 D2）：当日 + 之前 6 天，更老的启动时清除。 */
const BACKUP_RETENTION_DAYS = 7;

/** 自动轮转目录名格式；``manual-*``（scripts/backup-data.sh 产物）不匹配 → 不参与轮转。 */
const BACKUP_DIR_RE = /^\d{4}-\d{2}-\d{2}$/;

/** SQLite 一套库的全部文件——只拷 .db 不拷 -wal 会丢未 checkpoint 的页。 */
const DB_FILE_RE = /\.db(-wal|-shm)?$/;

let cachedRoot: string | undefined;
let dbDirLogged = false;
let backupAttempted = false;

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

/** 本地日期戳 ``YYYY-MM-DD``——备份按 dev 机本地日历算"天"，不用 UTC（半夜启动不跨日错位）。 */
function localDateStamp(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/**
 * ``.data/*.db*`` 启动时轮转备份（ADR-0048 D2）。
 *
 * 何时用：``resolveMastraDbDir()`` 首次调用时自动触发——LibSQLStore 尚未打开
 * 库文件，是文件级拷贝最安全的时点。测试可直接调用并注入 ``now``。
 *
 * 行为：拷贝 ``dbDir`` 顶层 SQLite 文件到 ``backups/<YYYY-MM-DD>/``，当日已有
 * 则跳过；随后清除超过 {@link BACKUP_RETENTION_DAYS} 天的日期目录
 * （``manual-*`` 手动备份不参与轮转）。
 *
 * 坑：**任何失败只 warn 不抛**——备份是保险不是闸门，不许拖挂启动。
 */
export function rotateDataBackups(dbDir: string, now: Date = new Date()): void {
  try {
    const backupsRoot = join(dbDir, "backups");
    const stamp = localDateStamp(now);
    const todayDir = join(backupsRoot, stamp);
    const dbFiles = readdirSync(dbDir).filter((f) => DB_FILE_RE.test(f));

    if (dbFiles.length === 0) {
      console.log(`[paths] data backup skip: ${dbDir} 下无 *.db`);
    } else if (existsSync(todayDir)) {
      console.log(`[paths] data backup skip: ${stamp} 当日已有`);
    } else {
      mkdirSync(todayDir, { recursive: true });
      for (const f of dbFiles) {
        copyFileSync(join(dbDir, f), join(todayDir, f));
      }
      console.log(`[paths] data backup ok: ${todayDir}（${dbFiles.length} 个文件）`);
    }

    if (existsSync(backupsRoot)) {
      const cutoffMs = now.getTime() - BACKUP_RETENTION_DAYS * 86_400_000;
      let pruned = 0;
      for (const name of readdirSync(backupsRoot)) {
        if (!BACKUP_DIR_RE.test(name)) continue;
        const dirMs = new Date(`${name}T00:00:00`).getTime();
        if (Number.isNaN(dirMs) || dirMs >= cutoffMs) continue;
        rmSync(join(backupsRoot, name), { recursive: true, force: true });
        pruned += 1;
      }
      if (pruned > 0) {
        console.log(`[paths] data backup prune: 清理 ${pruned} 个超过 ${BACKUP_RETENTION_DAYS} 天的目录`);
      }
    }
  } catch (err) {
    console.warn(`[paths] data backup 失败（不阻断启动）: ${String(err)}`);
  }
}

/**
 * mastra SQLite 库目录：``<orchestration 根>/.data``（gitignored），不存在则建。
 * 不能用 ``.mastra/``（mastra build 目录，启动即清，见模块头注释）。
 * 首次调用 log 实际路径 —— "历史去哪了"类排查的第一证据；
 * 并触发一次 {@link rotateDataBackups}（库文件被本进程打开之前）。
 */
export function resolveMastraDbDir(): string {
  const dbDir = resolve(resolveOrchestrationRoot(), ".data");
  if (!existsSync(dbDir)) {
    mkdirSync(dbDir, { recursive: true });
  }
  if (!backupAttempted) {
    backupAttempted = true;
    rotateDataBackups(dbDir);
  }
  if (!dbDirLogged) {
    dbDirLogged = true;
    console.log(`[paths] mastra db dir: ${dbDir} (cwd=${process.cwd()})`);
  }
  return dbDir;
}

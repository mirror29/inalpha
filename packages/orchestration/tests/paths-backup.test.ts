import { existsSync, mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { rotateDataBackups } from "../src/mastra/paths.js";

/** 固定"现在"= 2026-06-12 正午，断言用本地日期戳与之对齐。 */
const NOW = new Date(2026, 5, 12, 12, 0, 0);
const TODAY = "2026-06-12";

let dataDir: string;
let backupsRoot: string;

beforeEach(() => {
  dataDir = mkdtempSync(join(tmpdir(), "inalpha-backup-"));
  // review major：备份根必须在 dbDir 树外（兄弟目录），rm -rf dbDir 不连坐
  backupsRoot = mkdtempSync(join(tmpdir(), "inalpha-backup-root-"));
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  rmSync(dataDir, { recursive: true, force: true });
  rmSync(backupsRoot, { recursive: true, force: true });
  vi.restoreAllMocks();
});

describe("rotateDataBackups（ADR-0048 D2）", () => {
  it("首次调用把 *.db 及 -wal/-shm 拷进当日目录，无关文件不拷", () => {
    writeFileSync(join(dataDir, "inalpha-memory.db"), "mem");
    writeFileSync(join(dataDir, "inalpha-memory.db-wal"), "wal");
    writeFileSync(join(dataDir, "inalpha-traces.db"), "traces");
    writeFileSync(join(dataDir, "notes.txt"), "其他文件");

    rotateDataBackups(dataDir, backupsRoot, NOW);

    const backed = readdirSync(join(backupsRoot, TODAY)).sort();
    expect(backed).toEqual(["inalpha-memory.db", "inalpha-memory.db-wal", "inalpha-traces.db"]);
    // 原子提交后不残留 .tmp
    expect(existsSync(join(backupsRoot, `${TODAY}.tmp`))).toBe(false);
  });

  it("当日已有备份则跳过——首份不被同日二次启动覆盖", () => {
    writeFileSync(join(dataDir, "a.db"), "v1");
    rotateDataBackups(dataDir, backupsRoot, NOW);

    // 第一次备份后库继续被写、并新增了第二个库 → 同日再启动不应动已有备份
    writeFileSync(join(dataDir, "a.db"), "v2-当日后续写入");
    writeFileSync(join(dataDir, "b.db"), "新库");
    rotateDataBackups(dataDir, backupsRoot, NOW);

    expect(readdirSync(join(backupsRoot, TODAY))).toEqual(["a.db"]);
  });

  it("清理超过 7 天的日期目录与 crash 遗留 .tmp，保留期内与 manual-* 不动", () => {
    writeFileSync(join(dataDir, "a.db"), "x");
    for (const name of [
      "2026-06-01",
      "2026-06-04",
      "2026-06-06",
      "2026-06-11.tmp", // 昨日中途 crash 的遗留物
      "manual-20260601-090000",
    ]) {
      mkdirSync(join(backupsRoot, name), { recursive: true });
    }

    rotateDataBackups(dataDir, backupsRoot, NOW);

    expect(readdirSync(backupsRoot).sort()).toEqual([
      TODAY, // 当日新建
      "2026-06-06", // 7 天保留期内（06-05 是 cutoff）
      "manual-20260601-090000", // 手动备份不参与轮转
    ].sort());
  });

  it("当日已有不完整 .tmp（crash 遗留）不阻断本次备份——重建后原子顶替", () => {
    writeFileSync(join(dataDir, "a.db"), "x");
    mkdirSync(join(backupsRoot, `${TODAY}.tmp`), { recursive: true });
    writeFileSync(join(backupsRoot, `${TODAY}.tmp`, "半截.db"), "partial");

    rotateDataBackups(dataDir, backupsRoot, NOW);

    expect(readdirSync(join(backupsRoot, TODAY))).toEqual(["a.db"]);
    expect(existsSync(join(backupsRoot, `${TODAY}.tmp`))).toBe(false);
  });

  it("dbDir 不存在等任意失败只 warn 不抛（备份是保险不是闸门）", () => {
    expect(() =>
      rotateDataBackups(join(dataDir, "不存在的目录"), backupsRoot, NOW),
    ).not.toThrow();
    expect(console.warn).toHaveBeenCalledOnce();
  });
});

describe("resolveMastraDbDir 接线（review medium：备份入口冒烟）", () => {
  const ROOT_ENV = "INALPHA_ORCH_ROOT";
  let fakeRoot: string;
  let savedEnv: string | undefined;

  beforeEach(() => {
    fakeRoot = mkdtempSync(join(tmpdir(), "inalpha-orch-root-"));
    // resolveOrchestrationRoot 经 env 锚点解析需要真 package.json name
    writeFileSync(
      join(fakeRoot, "package.json"),
      JSON.stringify({ name: "@inalpha/orchestration" }),
    );
    mkdirSync(join(fakeRoot, ".data"));
    writeFileSync(join(fakeRoot, ".data", "a.db"), "v1");
    savedEnv = process.env[ROOT_ENV];
    process.env[ROOT_ENV] = fakeRoot;
  });

  afterEach(() => {
    if (savedEnv === undefined) delete process.env[ROOT_ENV];
    else process.env[ROOT_ENV] = savedEnv;
    rmSync(fakeRoot, { recursive: true, force: true });
  });

  it("首调触发一次备份到 .data 兄弟目录 .data-backups，再调不重复", async () => {
    vi.resetModules(); // 取新模块实例，绕开本文件其他用例外的 cachedRoot/backupAttempted 状态
    const { resolveMastraDbDir } = await import("../src/mastra/paths.js");

    const dbDir = resolveMastraDbDir();
    expect(dbDir).toBe(join(fakeRoot, ".data"));

    const backupsRoot = join(fakeRoot, ".data-backups");
    const dayDirs = readdirSync(backupsRoot);
    expect(dayDirs).toHaveLength(1);
    expect(readdirSync(join(backupsRoot, dayDirs[0]!))).toEqual(["a.db"]);

    // 二次调用不重复触发：新库文件不会出现在已有备份里
    writeFileSync(join(fakeRoot, ".data", "b.db"), "新库");
    resolveMastraDbDir();
    expect(readdirSync(join(backupsRoot, dayDirs[0]!))).toEqual(["a.db"]);
  });
});

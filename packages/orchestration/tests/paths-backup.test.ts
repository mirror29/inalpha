import { mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { rotateDataBackups } from "../src/mastra/paths.js";

/** 固定"现在"= 2026-06-12 正午，断言用本地日期戳与之对齐。 */
const NOW = new Date(2026, 5, 12, 12, 0, 0);
const TODAY = "2026-06-12";

let dataDir: string;

beforeEach(() => {
  dataDir = mkdtempSync(join(tmpdir(), "inalpha-backup-"));
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  rmSync(dataDir, { recursive: true, force: true });
  vi.restoreAllMocks();
});

describe("rotateDataBackups（ADR-0048 D2）", () => {
  it("首次调用把 *.db 及 -wal/-shm 拷进当日目录，无关文件不拷", () => {
    writeFileSync(join(dataDir, "inalpha-memory.db"), "mem");
    writeFileSync(join(dataDir, "inalpha-memory.db-wal"), "wal");
    writeFileSync(join(dataDir, "inalpha-traces.db"), "traces");
    writeFileSync(join(dataDir, "notes.txt"), "其他文件");

    rotateDataBackups(dataDir, NOW);

    const backed = readdirSync(join(dataDir, "backups", TODAY)).sort();
    expect(backed).toEqual(["inalpha-memory.db", "inalpha-memory.db-wal", "inalpha-traces.db"]);
  });

  it("当日已有备份则跳过——首份不被同日二次启动覆盖", () => {
    writeFileSync(join(dataDir, "a.db"), "v1");
    rotateDataBackups(dataDir, NOW);

    // 第一次备份后库继续被写、并新增了第二个库 → 同日再启动不应动已有备份
    writeFileSync(join(dataDir, "a.db"), "v2-当日后续写入");
    writeFileSync(join(dataDir, "b.db"), "新库");
    rotateDataBackups(dataDir, NOW);

    expect(readdirSync(join(dataDir, "backups", TODAY))).toEqual(["a.db"]);
  });

  it("清理超过 7 天的日期目录，保留期内与 manual-* 不动", () => {
    writeFileSync(join(dataDir, "a.db"), "x");
    const backupsRoot = join(dataDir, "backups");
    for (const name of ["2026-06-01", "2026-06-04", "2026-06-06", "manual-20260601-090000"]) {
      mkdirSync(join(backupsRoot, name), { recursive: true });
    }

    rotateDataBackups(dataDir, NOW);

    expect(readdirSync(backupsRoot).sort()).toEqual([
      TODAY, // 当日新建
      "2026-06-06", // 7 天保留期内（06-05 是 cutoff）
      "manual-20260601-090000", // 手动备份不参与轮转
    ].sort());
  });

  it("dbDir 不存在等任意失败只 warn 不抛（备份是保险不是闸门）", () => {
    expect(() => rotateDataBackups(join(dataDir, "不存在的目录"), NOW)).not.toThrow();
    expect(console.warn).toHaveBeenCalledOnce();
  });
});

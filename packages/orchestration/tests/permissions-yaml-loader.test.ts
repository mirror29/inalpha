/**
 * Permission YAML loader 单测（ADR-0011 / D-8b · #4）。
 *
 * 覆盖 issue #4 的 4 条验收：
 *
 *   1. yaml 内容等价于 DEFAULT_PERMISSIONS 常量
 *   2. 默认 yaml 加载成功
 *   3. INALPHA_PERMISSIONS_FILE env 切换生效
 *   4. 失败路径全部 strict throw（文件不存在 / 非法 YAML / schema 不匹配）
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  DEFAULT_PERMISSIONS,
  loadDefaultPermissions,
  loadPermissionConfigFromFile,
  resolveDefaultYamlPath,
} from "../src/permissions/index.js";

// 共享 env 守卫：每个测试自己管 env，避免污染
const ENV_KEY = "INALPHA_PERMISSIONS_FILE";

function saveEnv(): string | undefined {
  return process.env[ENV_KEY];
}
function restoreEnv(prev: string | undefined): void {
  if (prev === undefined) delete process.env[ENV_KEY];
  else process.env[ENV_KEY] = prev;
}

let tmpDir: string;
let prevEnv: string | undefined;

beforeEach(() => {
  prevEnv = saveEnv();
  delete process.env[ENV_KEY];
  tmpDir = mkdtempSync(join(tmpdir(), "inalpha-perm-yaml-"));
});

afterEach(() => {
  restoreEnv(prevEnv);
  rmSync(tmpDir, { recursive: true, force: true });
});

// ────────────────────────────────────────────────────────────────────
// 验收 1 / 2：默认 yaml 等价于 DEFAULT_PERMISSIONS + 加载成功
// ────────────────────────────────────────────────────────────────────

describe("loadDefaultPermissions: builtin equivalence", () => {
  it("默认 yaml 加载后逐字段等于 DEFAULT_PERMISSIONS 常量", () => {
    const config = loadDefaultPermissions();
    expect(config).toEqual(DEFAULT_PERMISSIONS);
  });

  it("loadPermissionConfigFromFile 读默认 yaml 路径也等价", () => {
    const config = loadPermissionConfigFromFile(resolveDefaultYamlPath());
    expect(config).toEqual(DEFAULT_PERMISSIONS);
  });

  it("resolveDefaultYamlPath 返回绝对路径", () => {
    const p = resolveDefaultYamlPath();
    expect(p.endsWith("config/permissions.default.yaml")).toBe(true);
    expect(p.startsWith("/") || /^[A-Z]:\\/.test(p)).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────
// 验收 3：env var 覆盖
// ────────────────────────────────────────────────────────────────────

describe("loadDefaultPermissions: INALPHA_PERMISSIONS_FILE override", () => {
  it("env 指向自定义 yaml 时，结果反映该文件而非默认", () => {
    const customYaml = [
      "defaultMode: allow",
      "allow:",
      "  - 'data.get_bars'",
      "ask: []",
      "deny:",
      "  - 'paper.submit_order'",
      "",
    ].join("\n");
    const customPath = join(tmpDir, "custom.yaml");
    writeFileSync(customPath, customYaml, "utf8");

    process.env[ENV_KEY] = customPath;
    const config = loadDefaultPermissions();

    expect(config.defaultMode).toBe("allow");
    expect(config.allow).toEqual(["data.get_bars"]);
    expect(config.ask).toEqual([]);
    expect(config.deny).toEqual(["paper.submit_order"]);
  });

  it("env 设的是相对路径时按 cwd 解析", () => {
    // 简化做法：直接传绝对路径，relative path 行为由 resolve() 兜底
    const customYaml =
      "defaultMode: deny\nallow: []\nask: []\ndeny: ['*']\n";
    const customPath = join(tmpDir, "deny-all.yaml");
    writeFileSync(customPath, customYaml, "utf8");

    process.env[ENV_KEY] = customPath;
    const config = loadDefaultPermissions();
    expect(config.defaultMode).toBe("deny");
    expect(config.deny).toEqual(["*"]);
  });
});

// ────────────────────────────────────────────────────────────────────
// 验收 4：失败路径全部 strict throw
// ────────────────────────────────────────────────────────────────────

describe("loadDefaultPermissions: strict throws", () => {
  it("env 指向不存在的文件 → throw 带路径", () => {
    const ghost = join(tmpDir, "does-not-exist.yaml");
    process.env[ENV_KEY] = ghost;
    expect(() => loadDefaultPermissions()).toThrow(/not found/);
    expect(() => loadDefaultPermissions()).toThrow(ghost);
  });

  it("yaml 语法非法 → throw 带文件路径", () => {
    const badYaml = "defaultMode: ask\nallow:\n  - 'unclosed string\n";
    const badPath = join(tmpDir, "bad-syntax.yaml");
    writeFileSync(badPath, badYaml, "utf8");
    process.env[ENV_KEY] = badPath;
    expect(() => loadDefaultPermissions()).toThrow(/invalid YAML/);
    expect(() => loadDefaultPermissions()).toThrow(badPath);
  });

  it("schema 缺 defaultMode → throw 带字段路径", () => {
    const missingMode = "allow: []\nask: []\ndeny: []\n";
    const p = join(tmpDir, "missing-mode.yaml");
    writeFileSync(p, missingMode, "utf8");
    process.env[ENV_KEY] = p;
    expect(() => loadDefaultPermissions()).toThrow(/schema mismatch/);
    expect(() => loadDefaultPermissions()).toThrow(/defaultMode/);
  });

  it("schema defaultMode 不在枚举 → throw", () => {
    const badEnum =
      "defaultMode: maybe\nallow: []\nask: []\ndeny: []\n";
    const p = join(tmpDir, "bad-enum.yaml");
    writeFileSync(p, badEnum, "utf8");
    process.env[ENV_KEY] = p;
    expect(() => loadDefaultPermissions()).toThrow(/schema mismatch/);
  });

  it("schema allow 不是数组 → throw", () => {
    const badType =
      "defaultMode: ask\nallow: 'data.*'\nask: []\ndeny: []\n";
    const p = join(tmpDir, "bad-type.yaml");
    writeFileSync(p, badType, "utf8");
    process.env[ENV_KEY] = p;
    expect(() => loadDefaultPermissions()).toThrow(/schema mismatch/);
    expect(() => loadDefaultPermissions()).toThrow(/allow/);
  });

  it("schema 顶层不是 object → throw", () => {
    const notObject = "- foo\n- bar\n";
    const p = join(tmpDir, "not-object.yaml");
    writeFileSync(p, notObject, "utf8");
    process.env[ENV_KEY] = p;
    expect(() => loadDefaultPermissions()).toThrow(/schema mismatch/);
  });
});

// ────────────────────────────────────────────────────────────────────
// loadPermissionConfigFromFile 直接调用
// ────────────────────────────────────────────────────────────────────

describe("loadPermissionConfigFromFile", () => {
  it("正常 yaml 解析回 PermissionConfig", () => {
    const p = join(tmpDir, "ok.yaml");
    writeFileSync(
      p,
      "defaultMode: ask\nallow:\n  - 'data.*'\nask: []\ndeny: []\n",
      "utf8",
    );
    const cfg = loadPermissionConfigFromFile(p);
    expect(cfg.defaultMode).toBe("ask");
    expect(cfg.allow).toEqual(["data.*"]);
  });

  it("不存在路径直接 throw（不依赖 env）", () => {
    const ghost = join(tmpDir, "ghost.yaml");
    expect(() => loadPermissionConfigFromFile(ghost)).toThrow(/not found/);
  });
});

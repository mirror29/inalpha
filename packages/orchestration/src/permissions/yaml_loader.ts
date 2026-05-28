/**
 * Permission YAML 加载层（ADR-0011 / D-8b · #4）。
 *
 * 优先级：``INALPHA_PERMISSIONS_FILE`` env var → 包内默认 yaml → ``DEFAULT_PERMISSIONS`` 常量。
 *
 * 加载规则：
 *
 * 1. ``loadPermissionConfigFromFile(path)``：strict——文件不存在 / YAML 不合法 /
 *    schema 不匹配都 throw 带路径信息的明确错误，绝不静默 fallback。
 * 2. ``loadDefaultPermissions()``：
 *    - env 指定路径 → 走 strict 加载，失败 throw（生产场景必须立即暴露）
 *    - env 未设 + 默认 yaml 存在 → strict 加载，失败 throw
 *    - env 未设 + 默认 yaml 不存在 → 回退 ``DEFAULT_PERMISSIONS``，console.warn 一次
 *
 * yaml 路径用 ``import.meta.url`` 解析，不依赖 cwd——避免 vitest / mastra dev
 * 在不同 working dir 下飘移。
 */
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { parse as parseYaml, YAMLParseError } from "yaml";
import { PermissionConfigSchema } from "./schema.js";
import { DEFAULT_PERMISSIONS } from "./defaults.js";
import type { PermissionConfig } from "./types.js";

/**
 * 从给定绝对路径加载 + 校验 yaml。
 * 失败时抛带文件路径的 Error，调用方拿到能直接定位。
 */
export function loadPermissionConfigFromFile(absPath: string): PermissionConfig {
  if (!existsSync(absPath)) {
    throw new Error(`permissions yaml not found: ${absPath}`);
  }

  let raw: string;
  try {
    raw = readFileSync(absPath, "utf8");
  } catch (err) {
    throw new Error(`failed to read permissions yaml ${absPath}: ${String(err)}`);
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    if (err instanceof YAMLParseError) {
      throw new Error(`invalid YAML in ${absPath}: ${err.message}`);
    }
    throw new Error(`invalid YAML in ${absPath}: ${String(err)}`);
  }

  const result = PermissionConfigSchema.safeParse(parsed);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join(".") || "<root>"}: ${i.message}`)
      .join("\n");
    throw new Error(`permissions yaml schema mismatch in ${absPath}:\n${issues}`);
  }

  return result.data;
}

/** 返回包内 ``config/permissions.default.yaml`` 的绝对路径。 */
export function resolveDefaultYamlPath(): string {
  // this file: packages/orchestration/src/permissions/yaml_loader.ts
  // target:    packages/orchestration/config/permissions.default.yaml
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "config", "permissions.default.yaml");
}

let _warnedFallback = false;

/**
 * 主入口：按优先级解析当前进程应使用的 PermissionConfig。
 *
 * - ``INALPHA_PERMISSIONS_FILE`` 设了 → strict 加载该文件
 * - 否则 → strict 加载包内默认 yaml；不存在时 console.warn 一次 + fallback 到常量
 */
export function loadDefaultPermissions(): PermissionConfig {
  const envPath = process.env.INALPHA_PERMISSIONS_FILE?.trim();
  if (envPath) {
    return loadPermissionConfigFromFile(resolve(envPath));
  }

  const defaultPath = resolveDefaultYamlPath();
  if (existsSync(defaultPath)) {
    return loadPermissionConfigFromFile(defaultPath);
  }

  if (!_warnedFallback) {
    console.warn(
      `[permissions] default yaml not found at ${defaultPath}; ` +
        `falling back to built-in DEFAULT_PERMISSIONS constant`,
    );
    _warnedFallback = true;
  }
  return DEFAULT_PERMISSIONS;
}

/**
 * 第一道沙盒：源码 AST 静态审计（ADR-0020）。
 *
 * 在源码进入 SandboxProvider 之前先做 *静态* 检查：
 *
 * - **白名单 import**：只允许数学 / 数据处理常用模块
 * - **黑名单 import**：os / sys / subprocess / socket / urllib 等"出沙盒"类
 * - **黑名单 AST 节点**：exec / eval / compile / __import__ / 危险 dunder 访问
 * - **可选结构检查**：要求顶层定义某些函数（evolution loop 用 generate_signals）
 *
 * **为什么放在 TS 侧 spawn python3 跑**：
 *
 * - 不在沙盒进程内审计（审计跟用户代码同进程不安全）
 * - 不在 Node 侧实现 Python AST（现成 TS lib 都太老）
 * - spawn 开销 ~100ms 可接受
 *
 * **何时跳过审计**：
 * - language === "node"（spike 阶段只审计 python；node 仅靠运行时隔离）
 */
import { spawn } from "node:child_process";

import type { SandboxLanguage } from "./provider.js";

export type AuditOptions = {
  /** 必须 import 的语言（python 才审；node 自动跳过返 ok）。 */
  language: SandboxLanguage;
  /** 必须存在的顶层函数名（evolution loop 用 ["generate_signals"]）。 */
  requireFunctions?: readonly string[];
  /** 默认 5_000ms；审计本身超时也算 fail。 */
  timeoutMs?: number;
};

export type AuditResult = {
  /** ok=true 才允许进沙盒。 */
  ok: boolean;
  /** 拒绝原因列表；ok=true 时空数组。 */
  errors: readonly string[];
  /** 实际跑审计耗时（毫秒）；node 跳过审计时为 0。 */
  durationMs: number;
};

// ────────────────────────────────────────────────────────────────────
// Python AST 审计脚本（嵌入 TS，避免部署时还要拷 .py 文件）
//
// 通过 stdin 收 code，通过 argv[1] 收 JSON options，stdout 输出 JSON 结果。
// ────────────────────────────────────────────────────────────────────

const PYTHON_AUDIT_SCRIPT = String.raw`
import ast, json, sys

ALLOWED_IMPORTS = {
    # 标准库 - 数学 / 数据
    "math", "statistics", "decimal", "fractions",
    "collections", "itertools", "functools", "operator",
    "typing", "dataclasses", "enum",
    "datetime", "time",  # 只读时间 ok
    "json", "re",
    # 第三方 - 数值
    "numpy", "pandas", "scipy",
}
DENIED_IMPORTS = {
    # 出沙盒类
    "os", "sys", "subprocess", "socket", "urllib", "urllib3",
    "http", "httpx", "requests", "aiohttp",
    "ctypes", "importlib", "imp", "pkg_resources",
    "shutil", "tempfile", "pathlib",
    "pty", "fcntl", "select", "signal", "threading", "multiprocessing",
}
DENIED_NAMES = {"exec", "eval", "compile", "__import__", "open"}
# 沙盒越狱常见 dunder
DENIED_ATTRS = {
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__dict__", "__getattribute__",
    "__import__",
}

opts = json.loads(sys.argv[1])
require_functions = set(opts.get("requireFunctions") or [])
code = sys.stdin.read()

errors = []

try:
    tree = ast.parse(code)
except SyntaxError as e:
    print(json.dumps({"ok": False, "errors": [f"SyntaxError: {e.msg} (line {e.lineno})"]}))
    sys.exit(0)

found_functions = set()

for node in ast.walk(tree):
    # 顶层定义的函数（不算 nested）
    if isinstance(node, ast.FunctionDef) and node in getattr(tree, "body", []):
        found_functions.add(node.name)

    # import x / import x.y
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in DENIED_IMPORTS:
                errors.append(f"denied import: {alias.name}")
            elif root not in ALLOWED_IMPORTS:
                errors.append(f"non-whitelisted import: {alias.name}")

    # from x import y
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            root = node.module.split(".")[0]
            if root in DENIED_IMPORTS:
                errors.append(f"denied import: from {node.module}")
            elif root not in ALLOWED_IMPORTS:
                errors.append(f"non-whitelisted import: from {node.module}")

    # exec / eval / compile / __import__ / open 直接 Name 调用
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in DENIED_NAMES:
            errors.append(f"denied call: {node.func.id}()")

    # 危险 dunder 属性访问（沙盒越狱常见路径）
    elif isinstance(node, ast.Attribute) and node.attr in DENIED_ATTRS:
        errors.append(f"denied attribute access: .{node.attr}")

# 结构性检查：必须定义某些顶层函数
missing = require_functions - found_functions
if missing:
    errors.append(f"missing required top-level function(s): {sorted(missing)}")

print(json.dumps({"ok": len(errors) == 0, "errors": errors}))
`.trim();

/**
 * 对源码做静态 AST 审计。
 *
 * @param code   待审计的源码（python 时实际审计；node 跳过）
 * @param opts   审计选项
 * @returns      AuditResult；**ok=false 时**调用方应当拒绝进入沙盒
 */
export async function auditCode(code: string, opts: AuditOptions): Promise<AuditResult> {
  const start = performance.now();

  // node 暂不审计；返回 ok 但 durationMs=0 让 caller 知道没真跑
  if (opts.language === "node") {
    return { ok: true, errors: [], durationMs: 0 };
  }

  const timeoutMs = opts.timeoutMs ?? 5_000;
  const optsJson = JSON.stringify({
    requireFunctions: opts.requireFunctions ?? [],
  });

  return new Promise<AuditResult>((resolve) => {
    let stdoutBuf = "";
    let stderrBuf = "";
    let settled = false;

    const child = spawn("python3", ["-c", PYTHON_AUDIT_SCRIPT, optsJson], {
      shell: false,
      env: { PATH: process.env.PATH ?? "" },
      stdio: ["pipe", "pipe", "pipe"],
    });

    const killTimer = setTimeout(() => {
      child.kill("SIGKILL");
    }, timeoutMs);

    child.stdout.on("data", (c: Buffer) => {
      stdoutBuf += c.toString("utf8");
    });
    child.stderr.on("data", (c: Buffer) => {
      stderrBuf += c.toString("utf8");
    });

    const settle = (result: AuditResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(killTimer);
      resolve(result);
    };

    child.on("error", (err) => {
      settle({
        ok: false,
        errors: [`audit spawn error: ${err.message}`],
        durationMs: performance.now() - start,
      });
    });

    child.on("close", (exitCode) => {
      if (exitCode !== 0) {
        settle({
          ok: false,
          errors: [
            `audit process exit ${exitCode}: ${stderrBuf.trim() || stdoutBuf.trim() || "no output"}`,
          ],
          durationMs: performance.now() - start,
        });
        return;
      }
      try {
        const parsed = JSON.parse(stdoutBuf) as { ok: boolean; errors: string[] };
        settle({
          ok: parsed.ok,
          errors: parsed.errors ?? [],
          durationMs: performance.now() - start,
        });
      } catch (err) {
        settle({
          ok: false,
          errors: [`audit result parse error: ${(err as Error).message}; raw=${stdoutBuf.slice(0, 200)}`],
          durationMs: performance.now() - start,
        });
      }
    });

    // 喂代码进去
    child.stdin.end(code, "utf8");
  });
}

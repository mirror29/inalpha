/**
 * Skill 加载层（ADR-0046 · progressive disclosure）。
 *
 * 职责拆成两半：
 *
 * 1. **清单（常驻 prompt）**：扫描 ``packages/orchestration/skills/`` 下各子目录的
 *    SKILL.md frontmatter，产出 "name — description" 一行清单，由
 *    ``buildSkillsPromptSection()`` 拼成 ``<skills>`` 段注入 orchestrator 的
 *    dynamic instructions。
 * 2. **正文（按需进 context）**：``readSkillFile()`` 给 ``skill.read`` tool 用，
 *    按需读 SKILL.md 正文 / references，带路径越界与扩展名护栏。
 *
 * 设计约束：
 * - **全 sync**：``buildInstructions()`` 是 sync 函数（orchestrator dynamic
 *   instructions），不能 await——所以这里用 readdirSync/readFileSync。
 * - **fail-open**：单个 skill frontmatter 坏 → warn + skip；整个目录缺失 → 空清单。
 *   skill 是增强不是依赖，永不把 orchestrator 拖挂（仿 MCP loadMcpTools 语义）。
 * - **memoize**：清单扫描结果进程内缓存（skill 目录构建期固化，运行期不变）；
 *   ``resetSkillsCache()`` 给测试 / 热重载用（仿 getMcpToolsCached/reset 模式）。
 * - 寻径用 ``import.meta.url``，不依赖 cwd（同 permissions/yaml_loader.ts 先例）；
 *   ``INALPHA_SKILLS_DIR`` env 可覆盖，兜底 bundler 下的路径漂移。
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { parse as parseYaml } from "yaml";

import {
  SKILL_READABLE_EXTENSIONS,
  SkillFrontmatterSchema,
  type SkillManifest,
} from "./schema.js";

/** skill.read 单次读取默认上限（字节）；超出截断并标 truncated */
export const DEFAULT_SKILL_FILE_MAX_BYTES = 65_536;

/** 返回包内 ``skills/`` 目录绝对路径；``INALPHA_SKILLS_DIR`` env 优先。 */
export function resolveDefaultSkillsDir(): string {
  const envDir = process.env.INALPHA_SKILLS_DIR?.trim();
  if (envDir) return resolve(envDir);
  // this file: packages/orchestration/src/skills/loader.ts
  // target:    packages/orchestration/skills/
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "skills");
}

/** frontmatter 围栏切分：返回 yaml 文本，无围栏返回 null。 */
function extractFrontmatter(raw: string): string | null {
  const m = /^---\r?\n([\s\S]*?)\r?\n---(\r?\n|$)/.exec(raw);
  return m?.[1] ?? null;
}

/** 递归收集 skill 目录内白名单扩展名文件（相对路径，posix 风格分隔）。 */
function collectReadableFiles(dir: string, root: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectReadableFiles(abs, root));
      continue;
    }
    if (!entry.isFile()) continue;
    const ext = extname(entry.name).toLowerCase();
    if (!(SKILL_READABLE_EXTENSIONS as readonly string[]).includes(ext)) continue;
    out.push(relative(root, abs).split(sep).join("/"));
  }
  return out.sort();
}

/**
 * 扫描 skills 目录，返回合法 skill 清单。
 *
 * 单个 skill 不合法（缺 SKILL.md / frontmatter 解析失败 / name≠目录名）→
 * console.warn + skip，不影响其余 skill；目录不存在 → 返回 []。
 */
export function loadSkillManifests(skillsDir?: string): SkillManifest[] {
  const root = skillsDir ?? resolveDefaultSkillsDir();
  if (!existsSync(root) || !statSync(root).isDirectory()) return [];

  const manifests: SkillManifest[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const dir = join(root, entry.name);
    const skillMd = join(dir, "SKILL.md");
    if (!existsSync(skillMd)) {
      console.warn(`[skills] ${entry.name}: 缺 SKILL.md，跳过`);
      continue;
    }
    try {
      const raw = readFileSync(skillMd, "utf8");
      const fmText = extractFrontmatter(raw);
      if (fmText === null) {
        console.warn(`[skills] ${entry.name}: SKILL.md 无 frontmatter 围栏，跳过`);
        continue;
      }
      const parsed = SkillFrontmatterSchema.safeParse(parseYaml(fmText));
      if (!parsed.success) {
        console.warn(
          `[skills] ${entry.name}: frontmatter 不合法，跳过 —— ${parsed.error.issues
            .map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`)
            .join("; ")}`,
        );
        continue;
      }
      if (parsed.data.name !== entry.name) {
        console.warn(
          `[skills] ${entry.name}: frontmatter name="${parsed.data.name}" 与目录名不符，跳过`,
        );
        continue;
      }
      manifests.push({
        name: parsed.data.name,
        description: parsed.data.description,
        dir,
        files: collectReadableFiles(dir, dir),
      });
    } catch (err) {
      console.warn(`[skills] ${entry.name}: 加载失败，跳过 —— ${String(err)}`);
    }
  }
  return manifests.sort((a, b) => a.name.localeCompare(b.name));
}

let _cache: SkillManifest[] | null = null;

/** Memoize 版清单扫描——首次真扫盘，之后复用（skill 目录运行期不变）。 */
export function getSkillManifestsCached(skillsDir?: string): SkillManifest[] {
  if (!_cache) _cache = loadSkillManifests(skillsDir);
  return _cache;
}

/** 重置清单缓存（测试 / 热重载用）。 */
export function resetSkillsCache(): void {
  _cache = null;
}

/**
 * 产出注入 system prompt 的 ``<skills>`` 段。
 *
 * 无 skill 时返回 ``""``（零 prompt 成本）。使用纪律写在段内而不是 INSTRUCTIONS，
 * 让"清单 + 纪律"作为一个整体随 skill 有无出现/消失。
 */
export function buildSkillsPromptSection(skillsDir?: string): string {
  const manifests = getSkillManifestsCached(skillsDir);
  if (manifests.length === 0) return "";

  const catalog = manifests.map((m) => `- ${m.name} — ${m.description}`).join("\n");
  return (
    `<skills>\n` +
    `可按需加载的投研方法论 skill（此处仅清单；正文必须先用 skill.read 读取，禁止凭清单一行话脑补内容）：\n` +
    `${catalog}\n\n` +
    `使用纪律：\n` +
    `- 用户意图命中某条 description → 先 skill.read 读其 SKILL.md，再按指引逐步执行；` +
    `references/ 下的文档按当前步骤需要再读，不要一次全读\n` +
    `- skill 是静态方法论不含任何数据：其中所有"查热点/行情/财报/新闻"步骤一律映射到 ` +
    `web.search / web.search_news / data.get_bars(fresh=true) / data.get_ticker / ` +
    `data.get_fundamentals / factor.* / research.deep_dive，禁止用训练记忆代答\n` +
    `- 按 skill 产出结论时保持用户语言，并标注数据截止时间\n` +
    `</skills>\n\n`
  );
}

export type ReadSkillFileResult =
  | {
      content: string;
      truncated: boolean;
      /** 该 skill 内还有哪些文件可读（让 agent 一次 read 即知全貌，省 list 调用） */
      availableFiles: string[];
    }
  | { error: string; availableSkills?: string[]; availableFiles?: string[] };

/**
 * 读取 skill 目录内单个文件，给 ``skill.read`` tool 用。
 *
 * 护栏（ADR-0046 信任边界）：
 * - ``file`` resolve 后必须仍落在该 skill 目录内（拒 ``../`` 穿越与绝对路径）
 * - 扩展名白名单 .md/.json/.txt；显式拒绝 ``scripts/`` 前缀
 * - 超过 maxBytes 截断并标 ``truncated``
 *
 * 所有失败走 ``{error}`` 返回值，不抛——错误信息本身是给 LLM 的修正提示。
 */
export function readSkillFile(
  name: string,
  file = "SKILL.md",
  opts?: { maxBytes?: number; skillsDir?: string },
): ReadSkillFileResult {
  const manifests = getSkillManifestsCached(opts?.skillsDir);
  const manifest = manifests.find((m) => m.name === name);
  if (!manifest) {
    return {
      error: `unknown skill "${name}"`,
      availableSkills: manifests.map((m) => m.name),
    };
  }

  if (file.startsWith("scripts/") || file.includes("/scripts/")) {
    return { error: `scripts/ 不可读（ADR-0046 信任边界）`, availableFiles: manifest.files };
  }
  const ext = extname(file).toLowerCase();
  if (!(SKILL_READABLE_EXTENSIONS as readonly string[]).includes(ext)) {
    return {
      error: `仅支持 ${SKILL_READABLE_EXTENSIONS.join("/")} 文本文件`,
      availableFiles: manifest.files,
    };
  }
  // 用 relative() 判穿越，别靠字符串前缀：manifest.dir 若带尾 sep（如 env
  // INALPHA_SKILLS_DIR 指向带尾斜杠的路径）`dir + sep` 会双斜杠误拒合法文件。
  const abs = resolve(manifest.dir, file);
  const rel = relative(manifest.dir, abs);
  if (rel === "" || rel === ".." || rel.startsWith(".." + sep) || isAbsolute(rel)) {
    return { error: `path escapes skill directory`, availableFiles: manifest.files };
  }
  if (!existsSync(abs) || !statSync(abs).isFile()) {
    return { error: `file not found: ${file}`, availableFiles: manifest.files };
  }

  const maxBytes = opts?.maxBytes ?? DEFAULT_SKILL_FILE_MAX_BYTES;
  const buf = readFileSync(abs);
  const truncated = buf.byteLength > maxBytes;
  const content = truncated ? buf.subarray(0, maxBytes).toString("utf8") : buf.toString("utf8");
  return { content, truncated, availableFiles: manifest.files };
}

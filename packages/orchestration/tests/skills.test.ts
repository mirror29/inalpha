/**
 * Skill 子系统测试（ADR-0046）。
 *
 * 三块：
 * 1. loader 单测 —— 扫描 / fail-open skip / 缓存 / prompt 段渲染（临时目录 fixtures）
 * 2. readSkillFile / skill.read tool —— 路径护栏 + 截断 + 错误形态
 * 3. vendored skill 体检 —— 真实 skills/ 目录里的每个 skill 过 frontmatter 校验、
 *    禁引 docs/miro（外来 skill 改写红线的 CI 守门半边，另一半在 check-consistency C7）
 */
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_SKILL_FILE_MAX_BYTES,
  buildSkillsPromptSection,
  getSkillManifestsCached,
  loadSkillManifests,
  readSkillFile,
  resetSkillsCache,
  resolveDefaultSkillsDir,
} from "../src/skills/index.js";
import { skillReadTool } from "../src/tools/index.js";
import { DEFAULT_PERMISSIONS, PermissionEngine } from "../src/permissions/index.js";
import { wireToolList } from "../src/mastra/wired-tools.js";

function writeSkill(root: string, dir: string, frontmatter: string, body = "# Body\n"): void {
  const d = join(root, dir);
  mkdirSync(d, { recursive: true });
  writeFileSync(join(d, "SKILL.md"), `---\n${frontmatter}\n---\n\n${body}`, "utf8");
}

let tmpRoot: string;

beforeEach(() => {
  tmpRoot = mkdtempSync(join(tmpdir(), "inalpha-skills-"));
  resetSkillsCache();
});

afterEach(() => {
  rmSync(tmpRoot, { recursive: true, force: true });
  resetSkillsCache();
  vi.restoreAllMocks();
});

describe("loadSkillManifests", () => {
  it("扫描合法 skill，收集白名单扩展名文件", () => {
    writeSkill(tmpRoot, "alpha", 'name: alpha\ndescription: "测试用方法论 A"');
    mkdirSync(join(tmpRoot, "alpha", "references"), { recursive: true });
    writeFileSync(join(tmpRoot, "alpha", "references", "guide.md"), "# guide", "utf8");
    writeFileSync(join(tmpRoot, "alpha", "rubric.json"), "{}", "utf8");
    // 非白名单扩展名不进 files
    writeFileSync(join(tmpRoot, "alpha", "tool.py"), "print(1)", "utf8");

    const manifests = loadSkillManifests(tmpRoot);
    expect(manifests).toHaveLength(1);
    expect(manifests[0]!.name).toBe("alpha");
    expect(manifests[0]!.files).toEqual(["SKILL.md", "references/guide.md", "rubric.json"]);
  });

  it("fail-open：坏 frontmatter / name 与目录名不符 / 缺 SKILL.md 都 warn + skip", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    writeSkill(tmpRoot, "good", 'name: good\ndescription: "ok"');
    writeSkill(tmpRoot, "bad-frontmatter", "name: [not closed");
    writeSkill(tmpRoot, "wrong-name", 'name: other-name\ndescription: "x"');
    mkdirSync(join(tmpRoot, "no-skill-md"));

    const manifests = loadSkillManifests(tmpRoot);
    expect(manifests.map((m) => m.name)).toEqual(["good"]);
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("name 非 kebab-case / description 超长被拒", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    writeSkill(tmpRoot, "Bad_Case", 'name: Bad_Case\ndescription: "x"');
    writeSkill(tmpRoot, "long-desc", `name: long-desc\ndescription: "${"x".repeat(1100)}"`);
    expect(loadSkillManifests(tmpRoot)).toEqual([]);
  });

  it("目录不存在返回空数组", () => {
    expect(loadSkillManifests(join(tmpRoot, "nope"))).toEqual([]);
  });
});

describe("getSkillManifestsCached / resetSkillsCache", () => {
  it("memoize：第二次调用不重扫盘", () => {
    writeSkill(tmpRoot, "alpha", 'name: alpha\ndescription: "A"');
    const first = getSkillManifestsCached(tmpRoot);
    expect(first).toHaveLength(1);
    // 缓存后新增 skill 不可见；reset 后可见
    writeSkill(tmpRoot, "beta", 'name: beta\ndescription: "B"');
    expect(getSkillManifestsCached(tmpRoot)).toHaveLength(1);
    resetSkillsCache();
    expect(getSkillManifestsCached(tmpRoot)).toHaveLength(2);
  });
});

describe("buildSkillsPromptSection", () => {
  it("渲染 <skills> 段：每 skill 一行 name — description + 使用纪律", () => {
    writeSkill(tmpRoot, "alpha", 'name: alpha\ndescription: "测试用方法论 A"');
    const section = buildSkillsPromptSection(tmpRoot);
    expect(section).toContain("<skills>");
    expect(section).toContain("- alpha — 测试用方法论 A");
    expect(section).toContain("skill.read");
    expect(section).toContain("fresh=true");
  });

  it("无 skill 时返回空串（零 prompt 成本）", () => {
    expect(buildSkillsPromptSection(tmpRoot)).toBe("");
  });
});

describe("readSkillFile 护栏", () => {
  beforeEach(() => {
    writeSkill(tmpRoot, "alpha", 'name: alpha\ndescription: "A"', "# Alpha 正文\n");
    mkdirSync(join(tmpRoot, "alpha", "references"), { recursive: true });
    writeFileSync(join(tmpRoot, "alpha", "references", "guide.md"), "# 指南", "utf8");
    mkdirSync(join(tmpRoot, "alpha", "scripts"), { recursive: true });
    writeFileSync(join(tmpRoot, "alpha", "scripts", "x.md"), "secret", "utf8");
  });

  it("缺省读 SKILL.md，带 availableFiles", () => {
    const out = readSkillFile("alpha", undefined, { skillsDir: tmpRoot });
    expect(out).toMatchObject({ truncated: false });
    expect((out as { content: string }).content).toContain("Alpha 正文");
    expect((out as { availableFiles: string[] }).availableFiles).toContain("references/guide.md");
  });

  it("读 references 子文件", () => {
    const out = readSkillFile("alpha", "references/guide.md", { skillsDir: tmpRoot });
    expect((out as { content: string }).content).toContain("指南");
  });

  it("拒 ../ 穿越与绝对路径", () => {
    writeFileSync(join(tmpRoot, "outside.md"), "leak", "utf8");
    const escape = readSkillFile("alpha", "../outside.md", { skillsDir: tmpRoot });
    expect(escape).toHaveProperty("error");
    const abs = readSkillFile("alpha", join(tmpRoot, "outside.md"), { skillsDir: tmpRoot });
    expect(abs).toHaveProperty("error");
  });

  it("拒 scripts/ 前缀与非白名单扩展名", () => {
    expect(readSkillFile("alpha", "scripts/x.md", { skillsDir: tmpRoot })).toHaveProperty("error");
    expect(readSkillFile("alpha", "SKILL.py", { skillsDir: tmpRoot })).toHaveProperty("error");
  });

  it("超 maxBytes 截断并标 truncated", () => {
    writeFileSync(join(tmpRoot, "alpha", "big.md"), "x".repeat(100), "utf8");
    const out = readSkillFile("alpha", "big.md", { skillsDir: tmpRoot, maxBytes: 10 });
    expect(out).toMatchObject({ truncated: true });
    expect((out as { content: string }).content).toHaveLength(10);
  });

  it("未知 skill 返回 {error, availableSkills}，不抛", () => {
    const out = readSkillFile("nope", undefined, { skillsDir: tmpRoot });
    expect(out).toMatchObject({ error: expect.stringContaining("nope") });
    expect((out as { availableSkills: string[] }).availableSkills).toContain("alpha");
  });
});

describe("skill.read tool", () => {
  it("permission：DEFAULT_PERMISSIONS 对 skill.read 是 allow", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    expect(engine.authorize("skill.read", { name: "serenity" }).decision).toBe("allow");
  });

  it("execute 走 readSkillFile（用真实 skills 目录读 serenity）", async () => {
    const out = (await skillReadTool.execute!({ name: "serenity" })) as {
      content?: string;
      error?: string;
    };
    expect(out.error).toBeUndefined();
    expect(out.content).toContain("供应链瓶颈");
  });

  it("wired 后 skill.read 进 audit log（ADR-0046 触发率统计）", async () => {
    const records: Record<string, unknown>[] = [];
    const [wrapped] = wireToolList([skillReadTool], {
      auditSink: (r) => records.push(r),
    });
    await wrapped!.execute!({ name: "serenity" });
    expect(records).toHaveLength(1);
    expect(records[0]!.event).toBe("PostToolUse");
    expect(records[0]!.tool).toBe("skill.read");
  });
});

describe("vendored skills 体检（CI 守门）", () => {
  it("真实 skills/ 目录每个 skill 过 frontmatter 校验，且含 serenity", () => {
    resetSkillsCache();
    const manifests = loadSkillManifests(resolveDefaultSkillsDir());
    expect(manifests.map((m) => m.name)).toContain("serenity");
  });

  it("skill 文档不引用 docs/miro 私有路径，正文都可读且不超截断上限", () => {
    resetSkillsCache();
    const manifests = loadSkillManifests(resolveDefaultSkillsDir());
    for (const m of manifests) {
      expect(m.description).not.toContain("docs/miro");
      for (const f of m.files) {
        const out = readSkillFile(m.name, f);
        expect(out, `${m.name}/${f}`).toHaveProperty("content");
        const { content, truncated } = out as { content: string; truncated: boolean };
        expect(truncated, `${m.name}/${f} 超过 ${DEFAULT_SKILL_FILE_MAX_BYTES}B`).toBe(false);
        expect(content, `${m.name}/${f} 引用了 docs/miro`).not.toContain("docs/miro");
      }
    }
  });
});

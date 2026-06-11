/**
 * Skill frontmatter schema（ADR-0046）。
 *
 * 采用开源 AgentSkills 约定：``SKILL.md`` 顶部 YAML frontmatter，``name`` /
 * ``description`` 必填。description 上限 1024 字符——它会整行进 system prompt
 * 常驻清单（progressive disclosure 的"目录页"），必须强制简洁防 prompt 膨胀。
 *
 * looseObject：容忍上游 skill 的扩展字段（license / compatibility / metadata 等），
 * 只校验本机制依赖的键，其余 passthrough 保存不解释（ADR-0046 out-of-scope）。
 */
import { z } from "zod";

export const SkillFrontmatterSchema = z.looseObject({
  /** kebab-case 标识，必须与 skill 目录名一致（loader 校验） */
  name: z
    .string()
    .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/)
    .max(64),
  /** 一行意图描述，进 system prompt 常驻清单；§3.2 禁写死触发短语 */
  description: z.string().min(1).max(1024),
  license: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).optional(),
});

export type SkillFrontmatter = z.infer<typeof SkillFrontmatterSchema>;

/** 扫描产物：一个已安装 skill 的清单项。 */
export type SkillManifest = {
  name: string;
  description: string;
  /** skill 目录绝对路径 */
  dir: string;
  /** 目录内可经 skill.read 读取的文件相对路径（白名单扩展名，递归） */
  files: string[];
};

/** skill.read 允许读取的扩展名（v1 只读文本，ADR-0046 信任边界） */
export const SKILL_READABLE_EXTENSIONS = [".md", ".json", ".txt"] as const;

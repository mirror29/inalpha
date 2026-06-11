/**
 * Skill 子系统入口（ADR-0046）。
 *
 * 对外两条线：
 * - ``buildSkillsPromptSection()``：orchestrator dynamic instructions 注入清单段
 * - ``readSkillFile()``：``skill.read`` tool 的正文按需读取
 *
 * @module skills
 */
export {
  buildSkillsPromptSection,
  getSkillManifestsCached,
  loadSkillManifests,
  readSkillFile,
  resetSkillsCache,
  resolveDefaultSkillsDir,
  DEFAULT_SKILL_FILE_MAX_BYTES,
} from "./loader.js";
export type { ReadSkillFileResult } from "./loader.js";
export { SkillFrontmatterSchema, SKILL_READABLE_EXTENSIONS } from "./schema.js";
export type { SkillFrontmatter, SkillManifest } from "./schema.js";

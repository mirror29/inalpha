/**
 * Skill 读取 tool（ADR-0046 · progressive disclosure 的"翻书"半边）。
 *
 * system prompt 里只常驻 skill 清单（name + description 一行）；正文与
 * references 经本 tool 按需进 context。读取逻辑全部在 skills/loader.ts，
 * 这里只做 tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { readSkillFile } from "../skills/index.js";

export const skillReadTool = createTool({
  id: "skill.read",
  description: `
    读取一个已安装投研方法论 skill 的正文或参考文档。system prompt 的 <skills>
    段只有一行 description 清单，完整工作流必须经本 tool 读到 context 里才能用。

    何时用：
    - 用户意图命中 <skills> 清单某条 description → 先读该 skill 的 SKILL.md 拿完整工作流
    - SKILL.md 正文指引"当前步骤读 references/ 某文档" → 再按需读该文件

    何时不用：
    - 查行情 / 财报 / 新闻 / 因子 → data.* / web.* / factor.*（skill 只有方法论，零数据）
    - 本轮对话已读过同一文件 → 直接用 context 里的内容，不要重复读

    坑：
    - file 是 skill 目录内相对路径；越界路径、scripts/、非 .md/.json/.txt 会被拒
    - 超长文件按 maxBytes 截断（truncated=true），返回的 availableFiles 列出该
      skill 全部可读文件，无需另查
    - skill 内容是静态方法论：其中所有"查数据"步骤必须落到本工具集执行，
      禁止凭训练记忆代答具体公司/数值/事件结论
  `.trim(),
  inputSchema: z.object({
    name: z
      .string()
      .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/)
      .max(64)
      .describe("skill 名，见 system prompt <skills> 清单"),
    file: z
      .string()
      .max(256)
      .optional()
      .describe("skill 目录内相对路径，缺省读 SKILL.md；如 references/evidence-ladder.md"),
  }),
  execute: async (inputData) => {
    try {
      return readSkillFile(inputData.name, inputData.file ?? "SKILL.md");
    } catch (err) {
      return { error: String(err) };
    }
  },
});

export const skillTools = [skillReadTool] as const;

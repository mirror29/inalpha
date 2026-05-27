/**
 * Permission YAML schema（ADR-0011 / D-8b · #4）。
 *
 * 把 ``PermissionConfig``（``types.ts``）映射成 zod 校验器，给 yaml_loader 用。
 *
 * 这里只校结构：``defaultMode`` 在三态枚举内 + 三个列表是字符串数组。
 * 单条规则字符串本身的语法合法性（predicate 解析、tool pattern 形状）由
 * ``PermissionEngine`` 实例化时的 ``parseRule`` 抛错——不在 schema 阶段重复。
 */
import { z } from "zod";

export const PermissionConfigSchema = z.object({
  defaultMode: z.enum(["allow", "ask", "deny"]),
  allow: z.array(z.string()),
  ask: z.array(z.string()),
  deny: z.array(z.string()),
});

export type PermissionConfigParsed = z.infer<typeof PermissionConfigSchema>;

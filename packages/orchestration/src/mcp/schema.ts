/**
 * 最小 JSON Schema → Zod 转换器（MCP tool inputSchema 用）。
 *
 * 为什么自己写而不引第三方：
 * - MCP tool 的 ``inputSchema`` 几乎都是"扁平对象 + 基础类型"的标准 JSON Schema 子集，
 *   覆盖这个子集只需几十行，引 ``json-schema-to-zod`` 这类（偏代码生成）依赖反而是
 *   额外供应链面 + 维护负担（呼应 Inalpha 轻依赖取向）。
 * - createTool 的 ``inputSchema`` 接受 Standard Schema（zod v4 即是），所以转出 zod 即可。
 *
 * 覆盖范围：object / string(+enum) / number / integer / boolean / array / 顶层 enum /
 * anyOf|oneOf(best-effort union)。**不认识的一律退化为 ``z.any()``**——保证永不抛错，
 * 最坏情况是 LLM 少一点 schema 提示，但 tool 仍可调用（MCP server 端会做真正校验）。
 *
 * @module mcp/schema
 */
import { z } from "zod";

/** JSON Schema 节点（只取我们用到的字段，其余忽略）。 */
interface JsonSchemaNode {
  type?: string | string[];
  properties?: Record<string, JsonSchemaNode>;
  required?: string[];
  items?: JsonSchemaNode;
  enum?: unknown[];
  anyOf?: JsonSchemaNode[];
  oneOf?: JsonSchemaNode[];
  description?: string;
  [key: string]: unknown;
}

/**
 * 把单个 JSON Schema 节点转成 zod 类型。
 *
 * @param node - JSON Schema 节点（任意，做防御式判断）
 * @returns 对应的 zod 类型；无法识别时返回 ``z.any()``
 */
function nodeToZod(node: JsonSchemaNode | undefined): z.ZodTypeAny {
  if (!node || typeof node !== "object") return z.any();

  // 顶层 enum（无 type 也可能有 enum）
  if (Array.isArray(node.enum) && node.enum.length > 0) {
    const literals = node.enum.map((v) => z.literal(v as never) as z.ZodTypeAny);
    return withDescription(unionOf(literals), node.description);
  }

  // anyOf / oneOf → union（best-effort）
  const variants = node.anyOf ?? node.oneOf;
  if (Array.isArray(variants) && variants.length > 0) {
    return withDescription(unionOf(variants.map(nodeToZod)), node.description);
  }

  // type 可能是数组（如 ["string","null"]）——取第一个非 null
  const type = Array.isArray(node.type)
    ? node.type.find((t) => t !== "null") ?? node.type[0]
    : node.type;
  // 记录 nullable：["string","null"] → z.string().nullable()，否则 LLM 传 null 会被
  // zod 先于 MCP server 拒掉（真实 MCP server 的 nullable 参数）。
  const isNullable = Array.isArray(node.type) && node.type.includes("null");

  let base: z.ZodTypeAny;
  switch (type) {
    case "object":
      base = objectToZod(node);
      break;
    case "string":
      base = z.string();
      break;
    case "number":
      base = z.number();
      break;
    case "integer":
      base = z.number().int();
      break;
    case "boolean":
      base = z.boolean();
      break;
    case "array":
      base = z.array(nodeToZod(node.items));
      break;
    default:
      base = z.any();
  }
  if (isNullable) base = base.nullable();
  return withDescription(base, node.description);
}

/** 把 1..N 个 zod 类型合成 union（单个直接返回，零个退化为 ``z.any()``）。 */
function unionOf(options: z.ZodTypeAny[]): z.ZodTypeAny {
  const [first, ...rest] = options;
  if (!first) return z.any();
  if (rest.length === 0) return first;
  return z.union([first, rest[0], ...rest.slice(1)] as [
    z.ZodTypeAny,
    z.ZodTypeAny,
    ...z.ZodTypeAny[],
  ]);
}

/** object 节点 → ``z.object({...}).passthrough()``，按 required 决定 optional。 */
function objectToZod(node: JsonSchemaNode): z.ZodTypeAny {
  const props = node.properties;
  if (!props || typeof props !== "object") {
    // 无 properties 的 object：放行任意键
    return z.object({}).passthrough();
  }
  const required = new Set(node.required ?? []);
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const [key, child] of Object.entries(props)) {
    const zChild = nodeToZod(child);
    shape[key] = required.has(key) ? zChild : zChild.optional();
  }
  // passthrough：容忍 schema 未声明的额外键，避免误拒 MCP server 的新字段
  return z.object(shape).passthrough();
}

/** 给 zod 类型挂上 description（若有）。 */
function withDescription(schema: z.ZodTypeAny, description?: string): z.ZodTypeAny {
  return description ? schema.describe(description) : schema;
}

/**
 * MCP tool 的顶层 inputSchema → zod。
 *
 * MCP 规范里 inputSchema 顶层应是 object；但做防御式处理：
 * - 顶层不是 object / 缺失 → ``z.object({}).passthrough()``（接受任意参数）
 *
 * @param schema - MCP tool 的 inputSchema（JSON Schema，任意）
 * @returns 顶层 zod object 类型，可直接喂给 createTool 的 inputSchema
 */
export function jsonSchemaToZod(schema: unknown): z.ZodTypeAny {
  if (!schema || typeof schema !== "object") {
    return z.object({}).passthrough();
  }
  const node = schema as JsonSchemaNode;
  const type = Array.isArray(node.type) ? node.type[0] : node.type;
  if (type && type !== "object") {
    // 顶层是非 object（罕见）——包一层让 createTool 仍拿到 object
    return z.object({}).passthrough();
  }
  return objectToZod(node);
}

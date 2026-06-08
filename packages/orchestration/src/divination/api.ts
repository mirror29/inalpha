/**
 * 占卜台 HTTP API —— 挂到 Mastra `server.apiRoutes` 上,跟 mastra dev 共用 4111 端口。
 *
 * 端点：
 *
 * - `POST /divination/cast`          —— 直算一卦 / 一牌(确定性,**无 LLM**)并落库,返回结果
 * - `GET  /divination/history`       —— 列某 subject 的历史占卜记录(倒序)
 * - `GET  /divination/:id`           —— 查单条历史记录
 *
 * 为什么独立于 agent tool(`tools/divination.ts`)：
 *
 * - 占卜台是**独立趣味模块**,点按钮应「瞬时出卦 + 动画」,不该走 LLM 会话往返。
 * - 引擎本就是纯函数(确定性、无 service / 无 secret),HTTP 直算最自然。
 * - 会话式深度解读仍由用户主动在对话栏触发 → orchestrator 调 `divination.*` tool。
 *
 * 单一引擎:本端点与 tool 共用同一 `castHexagram` / `drawTarot` + `DIVINATION_DISCLAIMER`,
 * 不重复任何卦表 / 牌库。
 *
 * 鉴权：跟 scheduler / permissions 路由一致(依赖 Mastra 内置 protected/public path);
 * `subject` 由 BFF(dashboard `/api/divination`)注入 = 控制台身份,做历史隶属/隔离。
 */
import type { Context, Handler } from "hono";

import { castHexagram } from "./hexagram.js";
import { DIVINATION_DISCLAIMER } from "./index.js";
import * as repo from "./repo.js";
import type { DivinationMode } from "./repo.js";
import { drawTarot, type TarotSpread } from "./tarot.js";

interface ApiRouteSpec {
  path: string;
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  handler: Handler;
}

/** 缺省身份 —— 与 dashboard `CONSOLE_SUBJECT` 默认值对齐(单租户 dev)。 */
const DEFAULT_SUBJECT = "console:dev";

/** 历史条数上限,避免一次拉太多。 */
const MAX_HISTORY = 100;
const DEFAULT_HISTORY = 30;

function badRequest(c: Context, msg: string): Response {
  return c.json({ error: "bad_request", message: msg }, 400);
}

/** mode → 引擎计算,产出带 disclaimer 的完整 DivinationView。 */
function compute(
  mode: DivinationMode,
  seed: string,
): { kind: "hexagram" | "tarot"; reading: unknown } {
  if (mode === "hexagram") {
    return { kind: "hexagram", reading: { ...castHexagram(seed), disclaimer: DIVINATION_DISCLAIMER } };
  }
  const spread: TarotSpread = mode === "tarotThree" ? "three" : "single";
  return { kind: "tarot", reading: { ...drawTarot(seed, spread), disclaimer: DIVINATION_DISCLAIMER } };
}

/** POST /divination/cast —— { mode, question, symbol?, subject? } */
const cast: Handler = async (c: Context) => {
  let body: Record<string, unknown>;
  try {
    body = (await c.req.json()) as Record<string, unknown>;
  } catch {
    return badRequest(c, "invalid JSON body");
  }

  const mode = body["mode"];
  if (mode !== "hexagram" && mode !== "tarotSingle" && mode !== "tarotThree") {
    return badRequest(c, "mode must be 'hexagram' | 'tarotSingle' | 'tarotThree'");
  }
  const question = typeof body["question"] === "string" ? body["question"].trim() : "";
  if (!question || question.length > 200) {
    return badRequest(c, "question must be a non-empty string (≤200 chars)");
  }
  const symbol =
    typeof body["symbol"] === "string" && body["symbol"].length <= 50
      ? body["symbol"]
      : null;
  const subject = typeof body["subject"] === "string" && body["subject"] ? body["subject"] : DEFAULT_SUBJECT;

  // seed 与 tool 一致 —— 同问得同卦(网页直算 / 对话占卜结果可复现)。
  const seed = `${question}|${symbol ?? ""}`;
  const { kind, reading } = compute(mode, seed);

  const record = await repo.insertDivination({
    subject,
    mode,
    question,
    symbol,
    kind,
    reading,
  });
  return c.json(record, 201);
};

/** GET /divination/history?subject=&limit= */
const history: Handler = async (c: Context) => {
  // TODO(multi-tenant): subject 必须从 JWT claims 派生,不能取自 query param —— 否则
  // 多租户下任何持有效 JWT 的调用方可传任意 subject 越权读他人占卜历史(同 getOne)。
  // 单租户 dev 下所有人 subject 相同、无实际影响;BFF 固定注入 CONSOLE_SUBJECT 也挡住了
  // 正常链路,但 mastra 端口直达时无此保护。
  const subject = c.req.query("subject") || DEFAULT_SUBJECT;
  const limitRaw = c.req.query("limit");
  let limit = limitRaw ? Number(limitRaw) : DEFAULT_HISTORY;
  if (Number.isNaN(limit) || limit <= 0) return badRequest(c, "invalid limit");
  limit = Math.min(limit, MAX_HISTORY);
  const records = await repo.listDivinations(subject, limit);
  return c.json({ records });
};

/** GET /divination/:id?subject= */
const getOne: Handler = async (c: Context) => {
  const id = c.req.param("id") ?? "";
  // TODO(multi-tenant): 同 history —— subject 应从 JWT claims 派生而非 query param。
  const subject = c.req.query("subject") || DEFAULT_SUBJECT;
  const record = await repo.getDivination(id, subject);
  return record === null ? c.json({ error: "not_found", id }, 404) : c.json(record);
};

export const divinationApiRoutes: ApiRouteSpec[] = [
  { path: "/divination/cast", method: "POST", handler: cast },
  { path: "/divination/history", method: "GET", handler: history },
  { path: "/divination/:id", method: "GET", handler: getOne },
];

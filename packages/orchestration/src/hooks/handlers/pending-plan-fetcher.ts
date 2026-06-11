/**
 * 生产用 ``PendingPlanFetcher`` —— 调 paper 服务 ``GET /plans`` 拉未执行 plan（issue #65）。
 *
 * 给 ``createPendingPlanCheckHandler``（Stop hook）和 ``pending-plan-notice``
 * processor（chat 路径）共用的注入实现：并发拉 ``pending_approval`` + ``approved``
 * 两个状态合并返回。
 *
 * 何时用：scheduler runner 的 Stop hook 循环、orchestrator 的输出 processor。
 * 何时不用：测试请直接注入返回固定数组的假 fetcher（handler 本来就是注入式）。
 *
 * 坑：paper ``/plans`` 没有 session 过滤——本 fetcher **忽略 sessionId 全局查**。
 * 单租户 dev console（agent 与控制台同账户）下行为正确；多租户化时需 paper 侧
 * 加 subject/session 维度过滤后再收紧这里。
 */
import { defaultServiceSubject, mintServiceToken } from "../../auth.js";
import { PaperClient } from "../../clients/paper.js";
import { getSettings } from "../../config.js";
import type { PendingPlanFetcher } from "./pending-plan-check.js";

/** 未执行的两个状态（executed / rejected / expired 是终态，不算残留）。 */
const PENDING_STATUSES = ["pending_approval", "approved"] as const;

/** 每个状态最多拉多少条——护栏只需要"有没有 + 几个 id"，不需要全量。 */
const FETCH_LIMIT = 20;

export type PaperPendingPlanFetcherOptions = {
  /** 固定 service token（scheduler 已有签好的 token 时传入）；缺省每次自签。 */
  token?: string;
};

/** 创建调 paper 服务的 PendingPlanFetcher。getSettings 延迟到调用时（CI 红线：模块顶层禁 eager）。 */
export function createPaperPendingPlanFetcher(
  opts: PaperPendingPlanFetcherOptions = {},
): PendingPlanFetcher {
  return async (_sessionId) => {
    const settings = getSettings();
    const token =
      opts.token ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
    const client = new PaperClient({ baseUrl: settings.paperServiceUrl, token });
    const lists = await Promise.all(
      PENDING_STATUSES.map((status) =>
        client.listPlans({ status, limit: FETCH_LIMIT }),
      ),
    );
    return lists.flat().map((p) => ({
      plan_id: p.plan_id,
      status: p.status,
      symbol: p.symbol,
      created_at: p.created_at,
    }));
  };
}

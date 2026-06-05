import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { getRemoteAgents } from "@ag-ui/mastra";
import { MastraClient } from "@mastra/client-js";

import { BACKENDS, CONSOLE_SUBJECT, getServiceToken } from "@/lib/backend";

/**
 * CopilotKit ↔ Mastra 桥（AG-UI 协议）。
 *
 * 链路:dashboard `<CopilotKit runtimeUrl="/api/copilotkit">` → 本 route →
 *      `@ag-ui/mastra` remote agent → mastra orchestrator(4111,`agent.stream`)。
 *
 * 设计要点:
 *  1. **remote agent**(指向 4111)而非把 mastra 实例 import 进 Next.js —— 两个服务解耦,
 *     dashboard 不背 mastra 依赖树。
 *  2. **每请求**重建 runtime,从而每次拿新鲜 JWT(`getServiceToken` 进程内缓存,到期前 60s 续签),
 *     规避长连接里 token 过期。
 *  3. **隔离**:`getRemoteAgents` 强制 `resourceId`(= JWT.sub = CONSOLE_SUBJECT),`threadId` 由前端
 *     `<CopilotKit threadId>` 传下并经 AG-UI 转发给 `agent.stream`,共同满足 memory.ts 的
 *     `assertScopedRequest`。单租户 dev 下 resourceId 固定;接真实多租户时改为从 session 派生。
 *  4. LLM 在 mastra 侧,本层不需要 model adapter —— 用 `ExperimentalEmptyAdapter` 占位。
 *
 * @returns CopilotKit runtime 的 POST handler
 */
export const POST = async (req: Request): Promise<Response> => {
  const token = await getServiceToken();

  const mastraClient = new MastraClient({
    baseUrl: BACKENDS.mastra,
    headers: { Authorization: `Bearer ${token}` },
  });

  const agents = await getRemoteAgents({
    mastraClient,
    resourceId: CONSOLE_SUBJECT,
  });

  const runtime = new CopilotRuntime({ agents });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};

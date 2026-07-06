import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
  type AgentsConfig,
} from "@copilotkit/runtime";
import { getRemoteAgents } from "@ag-ui/mastra";
import { MastraClient } from "@mastra/client-js";

import { BACKENDS, getServiceToken, getSessionSubject } from "@/lib/backend";

/**
 * 静音 @ag-ui/mastra 1.0.3 的良性日志噪音:它不认 mastra(v5 streamVNext)的
 * `text-start` / `text-end` 文本块标记,每条消息都 `console.warn` 一次
 * "[MastraAgent] Unrecognized stream chunk type: ..."。文本内容走 `text-delta`(它认),
 * 渲染不受影响 —— 纯噪音。模块加载时一次性包一层 console.warn 过滤掉,幂等。
 */
const _warn = console.warn.bind(console) as typeof console.warn;
if (!(console.warn as { __inalphaFiltered?: boolean }).__inalphaFiltered) {
  const filtered = ((...args: unknown[]) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("[MastraAgent] Unrecognized stream chunk type")
    ) {
      return;
    }
    _warn(...(args as Parameters<typeof console.warn>));
  }) as typeof console.warn & { __inalphaFiltered?: boolean };
  filtered.__inalphaFiltered = true;
  console.warn = filtered;
}

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
 *  3. **隔离**:`getRemoteAgents` 强制 `resourceId`(= 登录用户 sub,经 getSessionSubject()
 *     从 session 派生;dev 未登录回落 console:dev),`threadId` 由前端 `<CopilotKit threadId>`
 *     传下并经 AG-UI 转发给 `agent.stream`,共同满足 memory.ts 的 `assertScopedRequest`。
 *  4. LLM 在 mastra 侧,本层不需要 model adapter —— 用 `ExperimentalEmptyAdapter` 占位。
 *
 * ⚠️ **已知版本约束**:`@ag-ui/mastra`(目前最新 1.0.3)的 peerDependencies 把
 *    `@copilotkit/runtime` 硬钉到一个 pre-release(`0.0.0-mme-ag-ui-0-0-46-*`),没有任何
 *    已发布版本对齐到稳定的 1.59.x —— 这是上游打包遗留,`pnpm install` 会有 unmet peer 告警。
 *    我们固定用**最新稳定** `@copilotkit/{runtime,react-core}@1.59.x` + `@ag-ui/mastra@1.0.3`:
 *    运行时只用到 `getRemoteAgents` / `AbstractAgent` 等稳定 API,实测发消息 / 切会话 / 历史回填
 *    / 停止生成均正常(停止生成的 v1/v2 路径差异已在 ChatThread `handleStop` 兜底)。升级前
 *    若 @ag-ui/mastra 发布了对齐 1.59.x 的版本,应优先换到官方兼容对。
 *
 * @returns CopilotKit runtime 的 POST handler
 */
export const POST = async (req: Request): Promise<Response> => {
  // token 的 sub = 登录用户(或 dev 下 console:dev)。mastra identityMiddleware 据此
  // 注入 authSub,tool 层再据此打给 Python(resolveRequestToken),保证 agent 写操作落登录用户账户。
  const token = await getServiceToken();
  const resourceId = await getSessionSubject();

  const mastraClient = new MastraClient({
    baseUrl: BACKENDS.mastra,
    headers: { Authorization: `Bearer ${token}` },
  });

  const agents = await getRemoteAgents({
    mastraClient,
    resourceId,
  });

  // CopilotKit 1.59.5 起 `agents` 收紧为 NonEmptyRecord | Promise | factory;
  // getRemoteAgents 返回的是普通 Record(类型上可能为空,运行期非空),按 AgentsConfig 收口。
  const runtime = new CopilotRuntime({ agents: agents as unknown as AgentsConfig });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};
